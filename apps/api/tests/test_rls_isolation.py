"""Cross-tenant isolation tests — application-level enforcement.

The production stack uses the Supabase *service-role* key, which bypasses
PostgREST RLS. Tenant isolation is therefore enforced by the application
code: every service-role query appends ``.eq("tenant_id", tenant_id)`` where
``tenant_id`` is derived from the verified JWT → ``tenant_members`` lookup.

These tests assert that invariant holds end-to-end through the FastAPI
layer.  Three scenarios are exercised:

1. ``test_list_leads_tenant_isolation``
      GET /v1/leads as tenant-B returns an empty list when the in-memory
      fake DB only contains leads for tenant-A.  Proves the application
      code propagates the correct tenant_id into every SELECT.

2. ``test_get_lead_cross_tenant_returns_404``
      GET /v1/leads/{lead_id} as tenant-B for a lead that belongs to
      tenant-A returns 404, not the row.  The route double-filters:
      ``.eq("id", lead_id).eq("tenant_id", tenant_id)`` — both must match.

3. ``test_delete_lead_cross_tenant_returns_404``
      DELETE /v1/leads/{lead_id} as tenant-B returns 404 for a lead owned
      by tenant-A.  The route does a read-before-delete ownership check,
      so the same filter logic is exercised on a mutation path.

Design note
-----------
``_FilterFakeSupabase`` is a minimal variant of the e2e smoke test's
``FakeSupabase`` that additionally records ``.eq()`` calls so select
handlers can return tenant-scoped results.  This mirrors what the real
Postgres service-role + row-level filter achieves at the DB layer — the
test verifies the application code *passes the right arguments*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from src.core.security import AuthContext, get_current_user
from src.main import app

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TENANT_A = "tenant-aaaa-1111"
TENANT_B = "tenant-bbbb-2222"
USER_A = "user-aaaa-1111"
USER_B = "user-bbbb-2222"

# A single lead that belongs exclusively to tenant-A.
_LEAD_A: dict[str, Any] = {
    "id": "lead-a0a0a0a0",
    "tenant_id": TENANT_A,
    "pipeline_status": "new",
    "score": 0.85,
    "score_tier": "hot",
    "feedback": None,
    "contract_value_cents": None,
    "created_at": "2024-01-01T00:00:00+00:00",
    "outreach_channel": "email",
    "outreach_sent_at": None,
    "outreach_delivered_at": None,
    "outreach_opened_at": None,
    "outreach_clicked_at": None,
    "dashboard_visited_at": None,
    "whatsapp_initiated_at": None,
}


# ---------------------------------------------------------------------------
# FakeSupabase with eq-filter tracking
# ---------------------------------------------------------------------------


@dataclass
class _FakeResult:
    data: list[dict[str, Any]] = field(default_factory=list)
    count: int | None = None


class _FilterChain:
    """Fluent Supabase builder that records every ``.eq()`` call.

    Unlike the smoke test's ``_FakeChain``, this variant accumulates
    ``eq`` filters into ``self.filters`` so select handlers can simulate
    real tenant-scoped filtering.
    """

    def __init__(self, sb: "_FilterFakeSupabase", table: str) -> None:
        self._sb = sb
        self._table = table
        self._op: str = "select"
        self._payload: Any = None
        self.filters: dict[str, Any] = {}

    # --- terminal ops ---

    def select(self, *_a: Any, **_k: Any) -> "_FilterChain":
        self._op = "select"
        return self

    def insert(self, row: dict[str, Any]) -> "_FilterChain":
        self._op = "insert"
        self._payload = row
        return self

    def update(self, row: dict[str, Any]) -> "_FilterChain":
        self._op = "update"
        self._payload = row
        return self

    def delete(self) -> "_FilterChain":
        self._op = "delete"
        return self

    # --- filters — eq is tracked, rest are no-ops ---

    def eq(self, field: str, value: Any, *_a: Any, **_k: Any) -> "_FilterChain":
        self.filters[field] = value
        return self

    def neq(self, *_a: Any, **_k: Any) -> "_FilterChain":
        return self

    def in_(self, *_a: Any, **_k: Any) -> "_FilterChain":
        return self

    def is_(self, *_a: Any, **_k: Any) -> "_FilterChain":
        return self

    def or_(self, *_a: Any, **_k: Any) -> "_FilterChain":
        return self

    def gte(self, *_a: Any, **_k: Any) -> "_FilterChain":
        return self

    def lte(self, *_a: Any, **_k: Any) -> "_FilterChain":
        return self

    def order(self, *_a: Any, **_k: Any) -> "_FilterChain":
        return self

    def limit(self, *_a: Any, **_k: Any) -> "_FilterChain":
        return self

    def range(self, *_a: Any, **_k: Any) -> "_FilterChain":
        return self

    def maybe_single(self, *_a: Any, **_k: Any) -> "_FilterChain":
        return self

    def execute(self) -> _FakeResult:
        return self._sb._handle(self)


class _FilterFakeSupabase:
    """Minimal record-and-dispatch fake that honours ``eq`` filters.

    ``selects`` maps ``table_name → callable(chain) → _FakeResult``.
    The callable receives the full chain including ``chain.filters``,
    so it can simulate tenant-scoped results.
    """

    def __init__(
        self,
        selects: dict[str, Callable[[_FilterChain], _FakeResult]] | None = None,
    ) -> None:
        self.selects: dict[str, Callable[[_FilterChain], _FakeResult]] = selects or {}
        self.inserts: list[tuple[str, dict[str, Any]]] = []
        self.updates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.deletes: list[tuple[str, dict[str, Any]]] = []

    def table(self, name: str) -> _FilterChain:
        return _FilterChain(self, name)

    def _handle(self, chain: _FilterChain) -> _FakeResult:
        if chain._op == "select":
            fn = self.selects.get(chain._table)
            return fn(chain) if fn is not None else _FakeResult(data=[], count=0)
        if chain._op == "insert":
            assert chain._payload is not None
            self.inserts.append((chain._table, dict(chain._payload)))
            return _FakeResult(data=[dict(chain._payload)])
        if chain._op == "update":
            assert chain._payload is not None
            self.updates.append(
                (chain._table, dict(chain._payload), dict(chain.filters))
            )
            # Simulate: update returns the row only when both id AND tenant_id match.
            fn = self.selects.get(chain._table)
            if fn is not None:
                return fn(chain)
            return _FakeResult(data=[])
        if chain._op == "delete":
            self.deletes.append((chain._table, dict(chain.filters)))
            return _FakeResult(data=[])
        return _FakeResult(data=[])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_auth_for(tenant_id: str, user_id: str) -> Callable[[], AuthContext]:
    """Return a zero-arg async callable that yields an AuthContext for the given tenant."""

    async def _auth() -> AuthContext:
        return AuthContext(
            user_id=user_id,
            email=f"{user_id}@example.com",
            tenant_id=tenant_id,
            role="member",
        )

    return _auth


def _tenant_scoped_leads_select(chain: _FilterChain) -> _FakeResult:
    """Return tenant-A's lead only when the query filters on tenant-A.

    This simulates what Postgres + service-role + application-level
    ``.eq("tenant_id", …)`` achieves: different callers see different
    slices of the table.
    """
    tenant_filter = chain.filters.get("tenant_id")
    id_filter = chain.filters.get("id")

    # First narrow by tenant (simulates .eq("tenant_id", …))
    rows = [r for r in [_LEAD_A] if r["tenant_id"] == tenant_filter]

    # Then narrow by id if present (simulates .eq("id", lead_id))
    if id_filter is not None:
        rows = [r for r in rows if r["id"] == id_filter]

    return _FakeResult(data=rows, count=len(rows))


# ---------------------------------------------------------------------------
# 1. GET /v1/leads — list isolation
# ---------------------------------------------------------------------------


async def test_list_leads_tenant_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tenant-B's list query returns 0 leads when DB only has tenant-A leads.

    This is the primary read-isolation invariant: the application code must
    translate ``ctx.tenant_id`` into ``.eq("tenant_id", tenant_id)`` before
    hitting the DB.  If it forgets that clause, tenant-B would receive
    tenant-A's leads — a PII leak.
    """
    from src.routes import leads as leads_route

    fake_sb = _FilterFakeSupabase(selects={"leads": _tenant_scoped_leads_select})
    monkeypatch.setattr(leads_route, "get_service_client", lambda: fake_sb)

    # --- tenant-A sees their own lead ---
    app.dependency_overrides[get_current_user] = _fake_auth_for(TENANT_A, USER_A)
    try:
        with TestClient(app) as c:
            r_a = c.get(
                "/v1/leads",
                headers={"Authorization": "Bearer dummy-a"},
            )
        assert r_a.status_code == 200
        body_a = r_a.json()
        assert body_a["pagination"]["total"] == 1
        assert len(body_a["data"]) == 1
        assert body_a["data"][0]["id"] == _LEAD_A["id"]
    finally:
        app.dependency_overrides.clear()

    # --- tenant-B gets nothing (isolation holds) ---
    app.dependency_overrides[get_current_user] = _fake_auth_for(TENANT_B, USER_B)
    try:
        with TestClient(app) as c:
            r_b = c.get(
                "/v1/leads",
                headers={"Authorization": "Bearer dummy-b"},
            )
        assert r_b.status_code == 200
        body_b = r_b.json()
        assert body_b["pagination"]["total"] == 0, (
            "Tenant-B must not see Tenant-A's leads — application-level "
            "tenant isolation failed"
        )
        assert body_b["data"] == []
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 2. GET /v1/leads/{lead_id} — single-row isolation
# ---------------------------------------------------------------------------


async def test_get_lead_cross_tenant_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fetching a specific lead by ID as a different tenant returns 404.

    The route calls ``.eq("id", lead_id).eq("tenant_id", tenant_id)``.
    The second clause must prevent cross-tenant row reads even when the
    attacker knows (or guesses) a valid lead_id from another tenant.
    """
    from src.routes import leads as leads_route

    fake_sb = _FilterFakeSupabase(selects={"leads": _tenant_scoped_leads_select})
    monkeypatch.setattr(leads_route, "get_service_client", lambda: fake_sb)

    lead_id = _LEAD_A["id"]

    # --- tenant-A can fetch their own lead ---
    app.dependency_overrides[get_current_user] = _fake_auth_for(TENANT_A, USER_A)
    try:
        with TestClient(app) as c:
            r_a = c.get(
                f"/v1/leads/{lead_id}",
                headers={"Authorization": "Bearer dummy-a"},
            )
        assert r_a.status_code == 200
        assert r_a.json()["id"] == lead_id
    finally:
        app.dependency_overrides.clear()

    # --- tenant-B gets 404 for the same lead_id ---
    app.dependency_overrides[get_current_user] = _fake_auth_for(TENANT_B, USER_B)
    try:
        with TestClient(app) as c:
            r_b = c.get(
                f"/v1/leads/{lead_id}",
                headers={"Authorization": "Bearer dummy-b"},
            )
        assert r_b.status_code == 404, (
            f"Expected 404 for cross-tenant lead access, got {r_b.status_code}: "
            f"{r_b.text}"
        )
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 3. DELETE /v1/leads/{lead_id} — mutation isolation
# ---------------------------------------------------------------------------


async def test_delete_lead_cross_tenant_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attempting to delete a lead owned by another tenant returns 404.

    The delete route does a read-before-delete ownership check:
        ``.select("id, …").eq("id", lead_id).eq("tenant_id", tenant_id)``
    If ``res.data`` is empty it raises 404 — the lead is never touched.
    This guards against cross-tenant deletion regardless of whether the
    caller knows the target's lead_id.
    """
    from src.routes import leads as leads_route
    from src.services import audit_service

    fake_sb = _FilterFakeSupabase(selects={"leads": _tenant_scoped_leads_select})
    monkeypatch.setattr(leads_route, "get_service_client", lambda: fake_sb)

    # Stub out audit_log so the test doesn't need a real DB write path
    async def _noop_audit(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(audit_service, "log_action", _noop_audit)

    lead_id = _LEAD_A["id"]

    # --- tenant-B cannot delete tenant-A's lead ---
    app.dependency_overrides[get_current_user] = _fake_auth_for(TENANT_B, USER_B)
    try:
        with TestClient(app) as c:
            r = c.delete(
                f"/v1/leads/{lead_id}",
                headers={"Authorization": "Bearer dummy-b"},
            )
        assert r.status_code == 404, (
            f"Expected 404 for cross-tenant delete, got {r.status_code}: {r.text}"
        )
        # The fake DB must not have received a delete for tenant-A's lead
        assert not any(
            filters.get("id") == lead_id
            for (_, filters) in fake_sb.deletes
        ), "Cross-tenant delete reached the DB layer — isolation bypassed"
    finally:
        app.dependency_overrides.clear()
