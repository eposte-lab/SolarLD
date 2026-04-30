"""Append-only event log for GSE practices.

Sits between the practice service and the ``practice_events`` table:

  * ``record_event`` — single-row INSERT, returns the row dict.  Best-effort
    in the sense that callers should not rely on the row id for
    anything critical (we don't want to add a transactional dependency
    across the practice + the event log) — but we DO log loudly on
    failure, because a missing event means a missing timeline entry.

  * ``list_events`` — chronological query for the dashboard timeline.

  * ``EVENT_TYPES`` / ``EventType`` — the canonical list.  Keep this
    in sync with what the dashboard timeline knows how to render.

The ``deadline_*`` events are emitted from the deadlines service, not
here — but we list the constants in EVENT_TYPES so they're discoverable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Canonical event types — extend as the practice lifecycle grows.
# ---------------------------------------------------------------------------

# Practice lifecycle
EVT_PRACTICE_CREATED = "practice_created"
EVT_PRACTICE_STATUS_CHANGED = "practice_status_changed"
EVT_PRACTICE_CANCELLED = "practice_cancelled"

# Document lifecycle (one per template_code)
EVT_DOCUMENT_GENERATED = "document_generated"
EVT_DOCUMENT_REGENERATED = "document_regenerated"
EVT_DOCUMENT_GENERATION_FAILED = "document_generation_failed"
EVT_DOCUMENT_REVIEWED = "document_reviewed"
EVT_DOCUMENT_SENT = "document_sent"
EVT_DOCUMENT_ACCEPTED = "document_accepted"
EVT_DOCUMENT_REJECTED = "document_rejected"
EVT_DOCUMENT_AMENDED = "document_amended"
EVT_DOCUMENT_COMPLETED = "document_completed"

# Deadline lifecycle (emitted by deadlines service)
EVT_DEADLINE_CREATED = "deadline_created"
EVT_DEADLINE_SATISFIED = "deadline_satisfied"
EVT_DEADLINE_BREACHED = "deadline_breached"
EVT_DEADLINE_CANCELLED = "deadline_cancelled"

# Sprint 3+: missing-data fill-in (placeholder so the timeline UI
# already knows how to render it when we ship the smart form).
EVT_DATA_COLLECTED = "data_collected"

EVENT_TYPES: frozenset[str] = frozenset(
    {
        EVT_PRACTICE_CREATED,
        EVT_PRACTICE_STATUS_CHANGED,
        EVT_PRACTICE_CANCELLED,
        EVT_DOCUMENT_GENERATED,
        EVT_DOCUMENT_REGENERATED,
        EVT_DOCUMENT_GENERATION_FAILED,
        EVT_DOCUMENT_REVIEWED,
        EVT_DOCUMENT_SENT,
        EVT_DOCUMENT_ACCEPTED,
        EVT_DOCUMENT_REJECTED,
        EVT_DOCUMENT_AMENDED,
        EVT_DOCUMENT_COMPLETED,
        EVT_DEADLINE_CREATED,
        EVT_DEADLINE_SATISFIED,
        EVT_DEADLINE_BREACHED,
        EVT_DEADLINE_CANCELLED,
        EVT_DATA_COLLECTED,
    }
)


@dataclass(slots=True, frozen=True)
class PracticeEvent:
    id: str
    tenant_id: str
    practice_id: str
    document_id: str | None
    event_type: str
    payload: dict[str, Any]
    actor_user_id: str | None
    occurred_at: str
    created_at: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PracticeEvent":
        return cls(
            id=str(row["id"]),
            tenant_id=str(row["tenant_id"]),
            practice_id=str(row["practice_id"]),
            document_id=(str(row["document_id"]) if row.get("document_id") else None),
            event_type=row["event_type"],
            payload=row.get("payload") or {},
            actor_user_id=(
                str(row["actor_user_id"]) if row.get("actor_user_id") else None
            ),
            occurred_at=row["occurred_at"],
            created_at=row["created_at"],
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_event(
    *,
    tenant_id: str | UUID,
    practice_id: str | UUID,
    event_type: str,
    document_id: str | UUID | None = None,
    payload: dict[str, Any] | None = None,
    actor_user_id: str | UUID | None = None,
    occurred_at: str | None = None,
) -> PracticeEvent | None:
    """Append a single event to the log.

    Returns the created row (or None on error — best-effort by design;
    we don't want a logging failure to bubble up and abort the user's
    request).  Callers that *need* the event id (e.g. deadlines that
    reference triggered_by_event_id) should check the return value.

    ``event_type`` doesn't have to be in ``EVENT_TYPES`` — Postgres
    accepts anything via the free-form column — but we log a warning
    when an unknown type is used so we catch typos early.
    """
    if event_type not in EVENT_TYPES:
        log.warning(
            "practice_event.unknown_type",
            event_type=event_type,
            practice_id=str(practice_id),
        )

    sb = get_service_client()
    insert: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "practice_id": str(practice_id),
        "event_type": event_type,
        "payload": payload or {},
    }
    if document_id is not None:
        insert["document_id"] = str(document_id)
    if actor_user_id is not None:
        insert["actor_user_id"] = str(actor_user_id)
    if occurred_at is not None:
        insert["occurred_at"] = occurred_at

    try:
        res = sb.table("practice_events").insert(insert).execute()
    except Exception:
        log.exception(
            "practice_event.insert_failed",
            event_type=event_type,
            practice_id=str(practice_id),
        )
        return None

    if not res.data:
        log.warning(
            "practice_event.insert_returned_no_rows",
            event_type=event_type,
            practice_id=str(practice_id),
        )
        return None
    return PracticeEvent.from_row(res.data[0])


def list_events(
    *,
    tenant_id: str | UUID,
    practice_id: str | UUID,
    limit: int = 200,
) -> list[PracticeEvent]:
    """Chronological event log for one practice (ascending).

    The dashboard renders events bottom-to-top, but storing/serving
    ascending makes the SQL trivial (single index) and the UI can
    reverse cheaply on the client.
    """
    sb = get_service_client()
    res = (
        sb.table("practice_events")
        .select("*")
        .eq("tenant_id", str(tenant_id))
        .eq("practice_id", str(practice_id))
        .order("occurred_at", desc=False)
        .limit(limit)
        .execute()
    )
    return [PracticeEvent.from_row(r) for r in (res.data or [])]
