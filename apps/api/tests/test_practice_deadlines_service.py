"""Unit tests for ``practice_deadlines_service``.

We focus on the **projection logic** — given a practice_event, does
the rules engine open / satisfy / cancel the right deadline rows?
The Supabase calls themselves are mocked via a fake client so the
test runs offline (no SUPABASE_URL needed).

Things we deliberately do **not** test here:
  * The DB schema (covered by 0085 migration apply).
  * The cron job's notify-and-flip logic (covered manually via M11 in
    the Sprint 1 acceptance plan and by integration tests once we have
    a Supabase test project).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Tiny fake of supabase-py's chained query builder.
# ---------------------------------------------------------------------------
#
# The deadlines service does:
#   sb.table("practice_deadlines").upsert(payload, on_conflict="...").execute()
#   sb.table("practice_deadlines").update({...}).eq(...).eq(...).eq(...).execute()
#
# We don't need the full chainable surface — just enough so each call
# returns a `.execute()` whose `.data` matches what real Postgres would.


class _FakeQuery:
    def __init__(self, data: list[dict[str, Any]] | None = None) -> None:
        self.data = data or []
        self.calls: list[tuple[str, Any]] = []

    def upsert(self, payload, **_kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(("upsert", payload))
        # Mirror Postgres' RETURNING * — give the caller a row back.
        row = {"id": "deadline-1", **payload}
        return _FakeQuery([row])

    def update(self, payload, **_kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(("update", payload))
        return self

    def insert(self, payload, **_kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(("insert", payload))
        # Match the practice_events.id surface shape used by record_event.
        row = {
            "id": "ev-99",
            "tenant_id": payload.get("tenant_id"),
            "practice_id": payload.get("practice_id"),
            "document_id": payload.get("document_id"),
            "event_type": payload.get("event_type"),
            "payload": payload.get("payload") or {},
            "actor_user_id": None,
            "occurred_at": "2026-04-30T00:00:00+00:00",
            "created_at": "2026-04-30T00:00:00+00:00",
        }
        return _FakeQuery([row])

    def eq(self, *_a, **_kw):  # type: ignore[no-untyped-def]
        return self

    def in_(self, *_a, **_kw):  # type: ignore[no-untyped-def]
        return self

    def lte(self, *_a, **_kw):  # type: ignore[no-untyped-def]
        return self

    def order(self, *_a, **_kw):  # type: ignore[no-untyped-def]
        return self

    def limit(self, *_a, **_kw):  # type: ignore[no-untyped-def]
        return self

    def select(self, *_a, **_kw):  # type: ignore[no-untyped-def]
        return self

    def execute(self):  # type: ignore[no-untyped-def]
        return MagicMock(data=self.data)


class _FakeClient:
    """Routes every .table(name) to a per-table _FakeQuery, recording
    every operation so the test can inspect them."""

    def __init__(self) -> None:
        self.tables: dict[str, _FakeQuery] = {}

    def table(self, name: str) -> _FakeQuery:
        return self.tables.setdefault(name, _FakeQuery())


@pytest.fixture(autouse=True)
def _patch_supabase(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    """Patch get_service_client across both events + deadlines services."""
    from src.services import practice_deadlines_service as dl
    from src.services import practice_events_service as ev

    fake = _FakeClient()
    monkeypatch.setattr(dl, "get_service_client", lambda: fake)
    monkeypatch.setattr(ev, "get_service_client", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# DEADLINE_RULES — sanity check on the declarative table
# ---------------------------------------------------------------------------


def test_deadline_rules_have_distinct_kinds() -> None:
    """Two rules with the same kind would race-condition each other in
    the UNIQUE(practice_id, deadline_kind) UPSERT.  Pin them distinct."""
    from src.services.practice_deadlines_service import DEADLINE_RULES

    kinds = [r.kind for r in DEADLINE_RULES]
    assert len(kinds) == len(set(kinds))


def test_deadline_rules_cover_all_sprint2_documents() -> None:
    """Every Sprint 2 template that has a real-world SLA should appear
    as a trigger somewhere in DEADLINE_RULES.  Documents without an
    explicit SLA (schema_unifilare, attestazione_titolo, dm_37_08 —
    consegna al cliente only) intentionally absent."""
    from src.services.practice_deadlines_service import DEADLINE_RULES

    triggers = {r.trigger_template for r in DEADLINE_RULES}
    expected_with_sla = {
        "tica_areti",
        "comunicazione_comune",
        "modello_unico_p1",
        "modello_unico_p2",
        "transizione_50_ex_ante",
    }
    assert expected_with_sla.issubset(triggers)


# ---------------------------------------------------------------------------
# project_event_to_deadlines — open path
# ---------------------------------------------------------------------------


def _make_event(
    event_type: str,
    template_code: str | None = None,
    occurred_at: str = "2026-04-30T10:00:00+00:00",
) -> "Any":
    """Build a PracticeEvent dataclass with the minimum required fields."""
    from src.services.practice_events_service import PracticeEvent

    return PracticeEvent(
        id="ev-1",
        tenant_id="tenant-1",
        practice_id="practice-1",
        document_id="doc-1" if template_code else None,
        event_type=event_type,
        payload={"template_code": template_code} if template_code else {},
        actor_user_id=None,
        occurred_at=occurred_at,
        created_at=occurred_at,
    )


def test_document_sent_tica_opens_60d_deadline(
    _patch_supabase: _FakeClient,
) -> None:
    from src.services.practice_deadlines_service import (
        EVT_DOCUMENT_SENT,
        project_event_to_deadlines,
    )

    event = _make_event(EVT_DOCUMENT_SENT, template_code="tica_areti")
    summary = project_event_to_deadlines(event)

    # Exactly one deadline opened (the tica_response_60d rule).
    opened = [d for d in summary["opened"] if d["kind"] == "tica_response_60d"]
    assert len(opened) == 1
    # +60 calendar days from the trigger.
    expected_due = datetime(2026, 6, 29, 10, 0, tzinfo=timezone.utc)
    assert opened[0]["due_at"] == expected_due.isoformat()


def test_document_sent_comunicazione_comune_opens_30d_deadline(
    _patch_supabase: _FakeClient,
) -> None:
    from src.services.practice_deadlines_service import (
        EVT_DOCUMENT_SENT,
        project_event_to_deadlines,
    )

    event = _make_event(
        EVT_DOCUMENT_SENT,
        template_code="comunicazione_comune",
        occurred_at="2026-04-30T10:00:00+00:00",
    )
    summary = project_event_to_deadlines(event)

    kinds = {d["kind"] for d in summary["opened"]}
    assert "comune_acceptance_30d" in kinds


def test_document_sent_dm_37_08_opens_no_deadline(
    _patch_supabase: _FakeClient,
) -> None:
    """DM 37/08 is consegna al cliente — no automatic SLA, no deadline."""
    from src.services.practice_deadlines_service import (
        EVT_DOCUMENT_SENT,
        project_event_to_deadlines,
    )

    event = _make_event(EVT_DOCUMENT_SENT, template_code="dm_37_08")
    summary = project_event_to_deadlines(event)

    assert summary["opened"] == []


# ---------------------------------------------------------------------------
# project_event_to_deadlines — close path
# ---------------------------------------------------------------------------


def test_document_accepted_satisfies_open_deadline(
    _patch_supabase: _FakeClient,
) -> None:
    """When the installer marks comunicazione_comune accepted, the
    matching `comune_acceptance_30d` deadline must close."""
    from src.services.practice_deadlines_service import (
        EVT_DOCUMENT_ACCEPTED,
        project_event_to_deadlines,
    )

    # Pre-seed an open deadline that the close event should match.
    fake = _patch_supabase
    fake.tables["practice_deadlines"] = _FakeQuery(
        [
            {
                "id": "deadline-existing",
                "tenant_id": "tenant-1",
                "practice_id": "practice-1",
                "deadline_kind": "comune_acceptance_30d",
                "status": "open",
            }
        ]
    )

    event = _make_event(
        EVT_DOCUMENT_ACCEPTED, template_code="comunicazione_comune"
    )
    summary = project_event_to_deadlines(event)

    # The .satisfied list contains the closed deadline.
    assert any(d["kind"] == "comune_acceptance_30d" for d in summary["satisfied"])


# ---------------------------------------------------------------------------
# project_event_to_deadlines — practice cancellation
# ---------------------------------------------------------------------------


def test_practice_cancelled_closes_all_open_deadlines(
    _patch_supabase: _FakeClient,
) -> None:
    from src.services.practice_deadlines_service import (
        EVT_PRACTICE_CANCELLED,
        project_event_to_deadlines,
    )

    # Pre-seed two open deadlines that should cancel together.
    _patch_supabase.tables["practice_deadlines"] = _FakeQuery(
        [
            {
                "id": "d1",
                "deadline_kind": "tica_response_60d",
                "tenant_id": "tenant-1",
                "practice_id": "practice-1",
            },
            {
                "id": "d2",
                "deadline_kind": "comune_acceptance_30d",
                "tenant_id": "tenant-1",
                "practice_id": "practice-1",
            },
        ]
    )

    event = _make_event(EVT_PRACTICE_CANCELLED)
    summary = project_event_to_deadlines(event)

    assert summary["cancelled"] == 2


# ---------------------------------------------------------------------------
# Helper: _parse_iso accepts both Z and +00:00 forms
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_suffix() -> None:
    from src.services.practice_deadlines_service import _parse_iso

    a = _parse_iso("2026-04-30T10:00:00Z")
    b = _parse_iso("2026-04-30T10:00:00+00:00")
    assert a == b
