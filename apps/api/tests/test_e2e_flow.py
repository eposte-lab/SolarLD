"""End-to-end smoke tests for the new Sprint 8 surfaces.

Not a production-parameter load test — the goal here is to confirm
that the new CRM webhook dispatch and digest orchestration flows are
coherent from the entry point (service function) all the way through
HMAC signing / email composition. We use a tiny in-memory fake
Supabase client that records insert/update/select chains, and we
monkeypatch the outbound HTTP / email layer so no real network
happens.

Scenarios exercised:

1.  ``test_crm_webhook_dispatch_end_to_end``
      dispatch_event() for ``lead.contract_signed`` →
      Supabase resolves one active subscription →
      HMAC-SHA256 signed POST is sent →
      delivery row is persisted → subscription health is updated.

2.  ``test_daily_digest_skips_empty_and_sends_active``
      send_daily_digests() over two opted-in tenants →
      the one with zero activity is skipped, the one with signal
      receives a real Resend payload with the composed HTML subject
      line matching the daily format.

3.  ``test_notifications_count_route_through_api``
      GET /v1/notifications/count runs through the FastAPI auth
      dependency → require_tenant → Supabase count query → JSON.
      Sanity-checks route registration and context plumbing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from src.core.security import AuthContext, get_current_user
from src.main import app
from src.services import crm_webhook_service as crm_svc
from src.services import digest_service
from src.services import notifications_service
from src.services import resend_service


# ---------------------------------------------------------------------------
# In-memory Supabase fake — smallest surface that satisfies the flows
# under test. NOT a general implementation; adjust per test via the
# ``responses`` / ``inserts`` / ``updates`` recorders.
# ---------------------------------------------------------------------------


@dataclass
class _FakeResult:
    data: list[dict[str, Any]] = field(default_factory=list)
    count: int | None = None


class _FakeChain:
    """Builder returned by ``.table()``. Fluent no-op filters.

    All filter methods return ``self`` so the real code's chains work
    unchanged. The terminal ``execute()`` delegates to the owning
    ``FakeSupabase`` to decide what to return based on (table, op).
    """

    def __init__(self, sb: "FakeSupabase", table: str) -> None:
        self._sb = sb
        self._table = table
        self._op: str = "select"
        self._payload: Any = None
        self._kwargs: dict[str, Any] = {}

    # --- terminal ops ---
    def select(self, *_args: Any, **kwargs: Any) -> "_FakeChain":
        self._op = "select"
        self._kwargs.update(kwargs)
        return self

    def insert(self, row: dict[str, Any]) -> "_FakeChain":
        self._op = "insert"
        self._payload = row
        return self

    def update(self, row: dict[str, Any]) -> "_FakeChain":
        self._op = "update"
        self._payload = row
        return self

    def delete(self) -> "_FakeChain":
        self._op = "delete"
        return self

    # --- no-op filters / modifiers ---
    def eq(self, *_a: Any, **_k: Any) -> "_FakeChain":
        return self

    def in_(self, *_a: Any, **_k: Any) -> "_FakeChain":
        return self

    def is_(self, *_a: Any, **_k: Any) -> "_FakeChain":
        return self

    def or_(self, *_a: Any, **_k: Any) -> "_FakeChain":
        return self

    def gte(self, *_a: Any, **_k: Any) -> "_FakeChain":
        return self

    def lte(self, *_a: Any, **_k: Any) -> "_FakeChain":
        return self

    def order(self, *_a: Any, **_k: Any) -> "_FakeChain":
        return self

    def limit(self, *_a: Any, **_k: Any) -> "_FakeChain":
        return self

    # --- execute routes to owner ---
    def execute(self) -> _FakeResult:
        return self._sb._handle(self)


class FakeSupabase:
    """Record-and-dispatch fake.

    ``selects``: dict of ``table -> callable(chain) -> _FakeResult``.
      If the table is missing, returns an empty result.
    ``inserts``/``updates``/``deletes``: list of (table, payload) tuples
      recorded for assertions.
    """

    def __init__(
        self,
        selects: dict[str, Callable[[_FakeChain], _FakeResult]] | None = None,
    ) -> None:
        self.selects = selects or {}
        self.inserts: list[tuple[str, dict[str, Any]]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.deletes: list[str] = []

    def table(self, name: str) -> _FakeChain:
        return _FakeChain(self, name)

    def _handle(self, chain: _FakeChain) -> _FakeResult:
        if chain._op == "select":
            fn = self.selects.get(chain._table)
            if fn is None:
                return _FakeResult(data=[])
            return fn(chain)
        if chain._op == "insert":
            self.inserts.append((chain._table, dict(chain._payload)))
            # Mimic Supabase: insert returns the inserted row back as data.
            return _FakeResult(data=[dict(chain._payload)])
        if chain._op == "update":
            self.updates.append((chain._table, dict(chain._payload)))
            return _FakeResult(data=[dict(chain._payload)])
        if chain._op == "delete":
            self.deletes.append(chain._table)
            return _FakeResult(data=[])
        return _FakeResult(data=[])


# ---------------------------------------------------------------------------
# 1. CRM webhook dispatch end-to-end
# ---------------------------------------------------------------------------


async def test_crm_webhook_dispatch_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fire ``lead.contract_signed`` and watch the whole pipeline execute.

    We assert three things that together prove coherence:

      * HTTP was called exactly once with the correct URL + headers
        (in particular a verifiable HMAC signature).
      * A ``crm_webhook_deliveries`` row landed with the event type
        and status code.
      * The subscription's health counters were updated (2xx path
        resets failure_count to 0).
    """
    subscription = {
        "id": "sub-xyz",
        "tenant_id": "tenant-42",
        "url": "https://crm.example.com/hooks/solarlead",
        "secret": "deadbeef-super-secret",
        "events": ["lead.contract_signed", "lead.scored"],
        "failure_count": 0,
    }

    def _subs_select(_chain: _FakeChain) -> _FakeResult:
        return _FakeResult(data=[subscription])

    fake_sb = FakeSupabase(selects={"crm_webhook_subscriptions": _subs_select})
    monkeypatch.setattr(crm_svc, "get_service_client", lambda: fake_sb)

    # Capture the HTTP POST without touching the network.
    captured: dict[str, Any] = {}

    class _FakeResponse:
        status_code = 200
        text = "ok"

    class _FakeAsyncClient:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def post(
            self, url: str, *, content: bytes, headers: dict[str, str]
        ) -> _FakeResponse:
            captured["url"] = url
            captured["body"] = content
            captured["headers"] = headers
            return _FakeResponse()

    monkeypatch.setattr(crm_svc.httpx, "AsyncClient", _FakeAsyncClient)

    # --- drive the flow ---
    result = await crm_svc.dispatch_event(
        tenant_id="tenant-42",
        event_type="lead.contract_signed",
        occurred_at="2026-04-18T10:00:00+00:00",
        data={"lead_id": "lead-001", "contract_value_eur": 8500},
    )

    # --- assertions on the HTTP envelope ---
    assert captured["url"] == subscription["url"]
    headers = captured["headers"]
    assert headers["X-SolarLead-Event"] == "lead.contract_signed"
    assert headers["Content-Type"] == "application/json"
    # The signature must be a valid HMAC-SHA256 over the exact body.
    sig_header = headers["X-SolarLead-Signature"]
    assert sig_header.startswith("sha256=")
    expected = hmac.new(
        subscription["secret"].encode("utf-8"),
        captured["body"],
        hashlib.sha256,
    ).hexdigest()
    assert sig_header == f"sha256={expected}"
    # Body parses back to the canonical envelope.
    envelope = json.loads(captured["body"])
    assert envelope["event"] == "lead.contract_signed"
    assert envelope["tenant_id"] == "tenant-42"
    assert envelope["data"]["lead_id"] == "lead-001"

    # --- DB side effects ---
    # One delivery row logged + one subscription health update.
    delivery_rows = [row for (t, row) in fake_sb.inserts if t == "crm_webhook_deliveries"]
    assert len(delivery_rows) == 1
    assert delivery_rows[0]["event_type"] == "lead.contract_signed"
    assert delivery_rows[0]["status_code"] == 200
    assert delivery_rows[0]["error"] is None

    sub_updates = [row for (t, row) in fake_sb.updates if t == "crm_webhook_subscriptions"]
    assert len(sub_updates) == 1
    assert sub_updates[0]["failure_count"] == 0
    assert sub_updates[0]["last_status"] == "200"

    # --- dispatcher summary ---
    assert result["event"] == "lead.contract_signed"
    assert result["dispatched"] == 1
    assert result["results"][0]["healthy"] is True


# ---------------------------------------------------------------------------
# 2. Daily digest orchestration end-to-end
# ---------------------------------------------------------------------------


async def test_daily_digest_skips_empty_and_sends_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two opted-in tenants — one idle, one active — exercise every branch.

      * The empty-window tenant must be *skipped* (no email).
      * The active tenant must receive an email whose subject, body
        and tags match the daily digest format.
    """
    tenants = [
        {
            "id": "tenant-idle",
            "business_name": "Solare Idle",
            "contact_email": "ops+idle@example.com",
            "email_from_domain": "solarlead.it",
            "email_from_name": "SolarLead",
            "status": "active",
            "settings": {"feature_flags": {"daily_digest": True}},
        },
        {
            "id": "tenant-active",
            "business_name": "Solare Attiva",
            "contact_email": "ops+active@example.com",
            "email_from_domain": "solarlead.it",
            "email_from_name": "SolarLead",
            "status": "active",
            "settings": {"feature_flags": {"daily_digest": True}},
        },
        # Not opted in — must be filtered out by _flag_enabled.
        {
            "id": "tenant-optout",
            "business_name": "No Digest",
            "contact_email": "none@example.com",
            "status": "active",
            "settings": {"feature_flags": {}},
        },
    ]

    # Per-tenant stats: first tenant all-zero (skipped), second has signal.
    stats_by_tenant = {
        "tenant-idle": (0, 0, 0, 0, 0, 0, 0),
        # (leads_all, leads_hot, sent, opened, clicked, signed, cost_cents)
        "tenant-active": (12, 3, 8, 5, 2, 1, 1234),
    }

    call_idx = {"n": 0}

    def _leads_select(chain: _FakeChain) -> _FakeResult:
        """Dispatch leads/api_usage_log counts via call order.

        ``_compute_stats`` fires six count-style queries on ``leads``
        plus one ``api_usage_log`` select, always in the same order.
        We infer which tenant is being queried by watching for the
        idle-vs-active alternation: idle first (per tenants order),
        then active.
        """
        # This path is never reached — we route via table name below.
        return _FakeResult(count=0, data=[])

    def _tenants_select(_chain: _FakeChain) -> _FakeResult:
        return _FakeResult(data=tenants)

    # Track which tenant we're currently computing for. The orchestrator
    # processes tenants sequentially, so we can key off a mutable cursor.
    cursor = {"idx": 0, "leads_seen": 0, "usage_seen": 0}

    def _current_tenant_id() -> str:
        eligible = [t for t in tenants if t["settings"].get("feature_flags", {}).get("daily_digest")]
        return eligible[cursor["idx"]]["id"]

    def _leads_dispatch(_chain: _FakeChain) -> _FakeResult:
        stats = stats_by_tenant[_current_tenant_id()]
        # order within _compute_stats: all, hot, sent, opened, clicked, signed
        idx = cursor["leads_seen"] % 6
        cursor["leads_seen"] += 1
        return _FakeResult(count=stats[idx], data=[])

    def _usage_dispatch(_chain: _FakeChain) -> _FakeResult:
        stats = stats_by_tenant[_current_tenant_id()]
        cost_cents = stats[6]
        # End of this tenant's stat bundle — advance cursor.
        cursor["idx"] += 1
        cursor["leads_seen"] = 0
        return _FakeResult(
            data=[{"cost_cents": cost_cents}] if cost_cents else []
        )

    fake_sb = FakeSupabase(
        selects={
            "tenants": _tenants_select,
            "leads": _leads_dispatch,
            "api_usage_log": _usage_dispatch,
        }
    )
    monkeypatch.setattr(digest_service, "get_service_client", lambda: fake_sb)

    # Stub the email sender — capture every payload.
    sent: list[resend_service.SendEmailInput] = []

    async def _fake_send(payload: resend_service.SendEmailInput) -> Any:
        sent.append(payload)
        return resend_service.SendEmailResult(id="msg_fake_123")

    monkeypatch.setattr(digest_service, "send_email", _fake_send)

    # --- drive the flow ---
    result = await digest_service.send_daily_digests()

    # --- assertions ---
    assert result["window"] == "daily"
    results = result["results"]
    assert len(results) == 2  # opt-out tenant was filtered before send
    skipped = [r for r in results if r.get("skipped")]
    sent_rows = [r for r in results if r.get("sent")]
    assert len(skipped) == 1 and skipped[0]["tenant_id"] == "tenant-idle"
    assert len(sent_rows) == 1 and sent_rows[0]["tenant_id"] == "tenant-active"

    # Exactly one real email went out, to the active tenant.
    assert len(sent) == 1
    payload = sent[0]
    assert payload.to == ["ops+active@example.com"]
    assert payload.subject == "Riepilogo giornaliero — SolarLead"
    assert payload.from_address == "SolarLead <digest@solarlead.it>"
    assert payload.tags == {"kind": "digest", "window": "1d"}
    # HTML includes the numeric signal so we know composition plugged
    # the computed stats into format_digest_html (12 leads, 3 HOT, €12.34).
    assert "Solare Attiva" in payload.html
    assert "12" in payload.html and "3" in payload.html
    assert "€12.34" in payload.html
    assert payload.text is not None and "Nuovi lead:" in payload.text


# ---------------------------------------------------------------------------
# 3. Notifications count route — verify the HTTP layer + auth plumbing
# ---------------------------------------------------------------------------


def test_notifications_count_route_through_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hit /v1/notifications/count with a stubbed auth + supabase.

    The point is NOT to re-test the Supabase count query — it's to
    verify that the new route is wired up under the main app, respects
    the tenant requirement, and returns the JSON shape the dashboard
    bell consumes.
    """

    async def _fake_auth() -> AuthContext:
        return AuthContext(
            user_id="user-aaa",
            email="op@example.com",
            tenant_id="tenant-42",
            role="member",
        )

    # Use FastAPI's dependency_overrides so no real JWT decoding runs.
    app.dependency_overrides[get_current_user] = _fake_auth

    def _notif_select(_chain: _FakeChain) -> _FakeResult:
        # Simulate: 7 unread notifications for this tenant/user.
        return _FakeResult(count=7, data=[])

    from src.routes import notifications as notif_route

    fake_sb = FakeSupabase(selects={"notifications": _notif_select})
    monkeypatch.setattr(notif_route, "get_service_client", lambda: fake_sb)

    try:
        with TestClient(app) as c:
            r = c.get(
                "/v1/notifications/count",
                headers={"Authorization": "Bearer dummy-we-are-overridden"},
            )
        assert r.status_code == 200
        assert r.json() == {"unread": 7}
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 4. Notifications service → DB write is coherent
# ---------------------------------------------------------------------------


async def test_notify_writes_row_with_expected_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``notify()`` is the in-process hook agents use to raise bells.

    We assert it normalises an unknown severity back to ``info`` and
    hands the right shape to the insert call — the dashboard consumes
    these fields verbatim via RLS-filtered SELECT.
    """
    fake_sb = FakeSupabase()
    monkeypatch.setattr(notifications_service, "get_service_client", lambda: fake_sb)

    out = await notifications_service.notify(
        tenant_id="tenant-42",
        title="Nuovo contratto firmato",
        body="Lead Rossi Srl ha firmato.",
        severity="nope-not-real",  # bad severity must fall back to info
        href="/leads/lead-001",
        user_id=None,  # broadcast
        metadata={"lead_id": "lead-001"},
    )

    assert out is not None  # insert returned echoed row
    inserts = [row for (t, row) in fake_sb.inserts if t == "notifications"]
    assert len(inserts) == 1
    written = inserts[0]
    assert written["tenant_id"] == "tenant-42"
    assert written["severity"] == "info"  # normalised
    assert written["title"] == "Nuovo contratto firmato"
    assert written["href"] == "/leads/lead-001"
    assert written["metadata"] == {"lead_id": "lead-001"}
    assert written["user_id"] is None  # broadcast to tenant
