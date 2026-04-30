"""Deadline projection on top of ``practice_events``.

The DEADLINE_RULES table maps "when this trigger event fires, open a
deadline of this kind, due N calendar days later, that closes when the
closing event fires".  This keeps the business logic declarative and
testable — adding a new SLA is a single dict entry.

Public API:

  * ``project_event_to_deadlines(event)`` — call once per emitted
    practice_event.  Walks DEADLINE_RULES and either OPENS new
    deadlines (trigger match) or SATISFIES existing ones (closing
    match).  Returns a summary for logging.

  * ``recompute_open_for_practice(practice_id, tenant_id)`` — full
    rescan from existing events.  Useful for backfills and tests; the
    happy path uses ``project_event_to_deadlines`` incrementally.

  * ``mark_overdue_and_notify(now)`` — called from the daily cron.
    Flips ``open`` deadlines past ``due_at`` to ``overdue``, emits a
    ``deadline_breached`` event, and inserts a notification row so the
    bell lights up.

  * ``DEADLINE_RULES`` — declarative SLA table.  Documented inline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from .practice_events_service import (
    EVT_DEADLINE_BREACHED,
    EVT_DEADLINE_CANCELLED,
    EVT_DEADLINE_CREATED,
    EVT_DEADLINE_SATISFIED,
    EVT_DOCUMENT_ACCEPTED,
    EVT_DOCUMENT_COMPLETED,
    EVT_DOCUMENT_REJECTED,
    EVT_DOCUMENT_SENT,
    EVT_PRACTICE_CANCELLED,
    PracticeEvent,
    record_event,
)

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# DEADLINE_RULES — declarative SLAs.
# ---------------------------------------------------------------------------
#
# Schema:
#   kind                — stable id stored on practice_deadlines.deadline_kind
#   trigger_event_type  — opens the deadline when this event fires…
#   trigger_template    — …if the event's payload['template_code'] matches
#                         (None = matches any document)
#   offset_days         — calendar days after the trigger event's
#                         occurred_at; that becomes due_at
#   close_event_types   — list of event types that satisfy the deadline
#   close_template      — same gating as trigger_template
#   title               — Italian human-readable label for the bell/UI
#   reference           — citation shown in tooltips ("ARERA 109/2021 art. 7")
#
# Adding a new SLA is one dict entry + a test.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeadlineRule:
    kind: str
    trigger_event_type: str
    trigger_template: str | None
    offset_days: int
    close_event_types: tuple[str, ...]
    close_template: str | None
    title: str
    reference: str


DEADLINE_RULES: tuple[DeadlineRule, ...] = (
    # TICA — distributore must respond within 60 calendar days from
    # submission (ARERA delibera 109/2021 art. 7).  Closing event:
    # the installer marks the document accepted.
    DeadlineRule(
        kind="tica_response_60d",
        trigger_event_type=EVT_DOCUMENT_SENT,
        trigger_template="tica_areti",
        offset_days=60,
        close_event_types=(EVT_DOCUMENT_ACCEPTED, EVT_DOCUMENT_COMPLETED),
        close_template="tica_areti",
        title="Risposta TICA distributore",
        reference="ARERA 109/2021 art. 7 — 60 gg dalla domanda",
    ),
    # Comunicazione fine lavori al Comune — DPR 380/2001 art. 6,
    # silenzio-assenso a 30 gg.  Trigger: comunicazione_comune sent.
    DeadlineRule(
        kind="comune_acceptance_30d",
        trigger_event_type=EVT_DOCUMENT_SENT,
        trigger_template="comunicazione_comune",
        offset_days=30,
        close_event_types=(EVT_DOCUMENT_ACCEPTED, EVT_DOCUMENT_COMPLETED),
        close_template="comunicazione_comune",
        title="Silenzio-assenso Comune",
        reference="DPR 380/2001 art. 6 — 30 gg dalla comunicazione",
    ),
    # Modello Unico parte II — entro 30 gg dalla data fine lavori
    # (D.Lgs. 199/2021).  Trigger: parte I sent.
    DeadlineRule(
        kind="modello_unico_p2_due_30d",
        trigger_event_type=EVT_DOCUMENT_SENT,
        trigger_template="modello_unico_p1",
        offset_days=30,
        close_event_types=(EVT_DOCUMENT_SENT,),
        close_template="modello_unico_p2",
        title="Invio Modello Unico parte II",
        reference="D.Lgs. 199/2021 — entro 30 gg dalla fine lavori",
    ),
    # Transizione 5.0 ex-post — entro 60 gg da entrata in esercizio.
    DeadlineRule(
        kind="transizione_50_ex_post_60d",
        trigger_event_type=EVT_DOCUMENT_SENT,
        trigger_template="transizione_50_ex_ante",
        offset_days=60,
        close_event_types=(EVT_DOCUMENT_SENT,),
        close_template="transizione_50_ex_post",
        title="Comunicazione ex-post Transizione 5.0",
        reference="D.L. 19/2024 art. 38 — 60 gg da entrata in esercizio",
    ),
    # GSE Modello Unico parte II accettazione — silenzio assenso 30 gg.
    DeadlineRule(
        kind="modello_unico_p2_acceptance_30d",
        trigger_event_type=EVT_DOCUMENT_SENT,
        trigger_template="modello_unico_p2",
        offset_days=30,
        close_event_types=(EVT_DOCUMENT_ACCEPTED, EVT_DOCUMENT_COMPLETED),
        close_template="modello_unico_p2",
        title="Accettazione GSE Modello Unico",
        reference="D.Lgs. 199/2021 — silenzio-assenso 30 gg",
    ),
)


# ---------------------------------------------------------------------------
# Projection — call on every emitted event
# ---------------------------------------------------------------------------


def project_event_to_deadlines(event: PracticeEvent) -> dict[str, Any]:
    """Apply DEADLINE_RULES to a freshly-recorded event.

    Two pathways:
      1. **Trigger match** → INSERT (or UPSERT on `practice_id+kind`)
         a new ``open`` deadline with ``due_at = event.occurred_at +
         rule.offset_days``.  Emits an ``EVT_DEADLINE_CREATED``.
      2. **Close match** → UPDATE the matching ``open`` deadline to
         ``satisfied`` and emit ``EVT_DEADLINE_SATISFIED``.

    Practice cancellation (EVT_PRACTICE_CANCELLED) cancels every
    remaining open deadline for the practice.

    Returns ``{'opened': [...], 'satisfied': [...], 'cancelled': N}``
    for caller logging.
    """
    sb = get_service_client()
    summary: dict[str, Any] = {
        "opened": [],
        "satisfied": [],
        "cancelled": 0,
    }

    template_code = (event.payload or {}).get("template_code")

    # Practice-wide cancellation — close everything.
    if event.event_type == EVT_PRACTICE_CANCELLED:
        cancelled = (
            sb.table("practice_deadlines")
            .update({"status": "cancelled"})
            .eq("practice_id", event.practice_id)
            .eq("status", "open")
            .execute()
        )
        n = len(cancelled.data or [])
        summary["cancelled"] = n
        for row in cancelled.data or []:
            record_event(
                tenant_id=event.tenant_id,
                practice_id=event.practice_id,
                event_type=EVT_DEADLINE_CANCELLED,
                payload={
                    "deadline_id": row["id"],
                    "deadline_kind": row["deadline_kind"],
                    "reason": "practice_cancelled",
                },
            )
        return summary

    # Walk rules looking for triggers.
    for rule in DEADLINE_RULES:
        if rule.trigger_event_type == event.event_type and (
            rule.trigger_template is None
            or rule.trigger_template == template_code
        ):
            occurred_dt = _parse_iso(event.occurred_at)
            due_at = occurred_dt + timedelta(days=rule.offset_days)
            payload = {
                "tenant_id": event.tenant_id,
                "practice_id": event.practice_id,
                "deadline_kind": rule.kind,
                "due_at": due_at.isoformat(),
                "status": "open",
                "satisfied_at": None,
                "satisfied_by_event_id": None,
                "triggered_by_event_id": event.id,
                "metadata": {
                    "title": rule.title,
                    "reference": rule.reference,
                    "trigger_event_type": rule.trigger_event_type,
                    "trigger_template": rule.trigger_template,
                    "offset_days": rule.offset_days,
                },
            }
            if event.document_id:
                payload["document_id"] = event.document_id
            try:
                res = (
                    sb.table("practice_deadlines")
                    .upsert(payload, on_conflict="practice_id,deadline_kind")
                    .execute()
                )
            except Exception:
                log.exception(
                    "deadline.upsert_failed",
                    practice_id=event.practice_id,
                    deadline_kind=rule.kind,
                )
                continue
            if res.data:
                deadline_id = res.data[0]["id"]
                summary["opened"].append(
                    {
                        "id": deadline_id,
                        "kind": rule.kind,
                        "due_at": due_at.isoformat(),
                    }
                )
                record_event(
                    tenant_id=event.tenant_id,
                    practice_id=event.practice_id,
                    document_id=event.document_id,
                    event_type=EVT_DEADLINE_CREATED,
                    payload={
                        "deadline_id": deadline_id,
                        "deadline_kind": rule.kind,
                        "due_at": due_at.isoformat(),
                        "title": rule.title,
                    },
                )

        # Closing match.
        if event.event_type in rule.close_event_types and (
            rule.close_template is None or rule.close_template == template_code
        ):
            close_res = (
                sb.table("practice_deadlines")
                .update(
                    {
                        "status": "satisfied",
                        "satisfied_at": event.occurred_at,
                        "satisfied_by_event_id": event.id,
                    }
                )
                .eq("practice_id", event.practice_id)
                .eq("deadline_kind", rule.kind)
                .eq("status", "open")
                .execute()
            )
            for row in close_res.data or []:
                summary["satisfied"].append(
                    {"id": row["id"], "kind": rule.kind}
                )
                record_event(
                    tenant_id=event.tenant_id,
                    practice_id=event.practice_id,
                    document_id=event.document_id,
                    event_type=EVT_DEADLINE_SATISFIED,
                    payload={
                        "deadline_id": row["id"],
                        "deadline_kind": rule.kind,
                        "closed_by_event_id": event.id,
                    },
                )

    return summary


# ---------------------------------------------------------------------------
# Daily cron: flip open → overdue, notify
# ---------------------------------------------------------------------------


def mark_overdue_and_notify(now: datetime | None = None) -> dict[str, Any]:
    """Flip ``open`` deadlines past their due date to ``overdue``.

    For every newly-overdue row:
      * record an ``EVT_DEADLINE_BREACHED`` event,
      * insert a tenant-wide notification with severity=warning that
        deep-links to the practice detail page.

    Notifications about the same deadline aren't deduped at the SQL
    level — the cron should run once a day, so producing one notification
    per overdue deadline per day is the desired UX (the user gets a
    daily nag until they action it).  But we DO dedupe within a single
    cron tick by the ``deadline_id`` set we already updated, so a
    re-entrant cron run doesn't spam.

    Returns ``{"newly_overdue": N, "errors": M}``.
    """
    now = now or datetime.now(timezone.utc)
    sb = get_service_client()

    # 1. Find open deadlines that have aged past due_at.  We use the
    #    partial index (status='open') for an efficient scan.
    overdue_res = (
        sb.table("practice_deadlines")
        .select("*, practices(practice_number)")
        .eq("status", "open")
        .lte("due_at", now.isoformat())
        .limit(500)
        .execute()
    )
    rows = overdue_res.data or []
    if not rows:
        return {"newly_overdue": 0, "errors": 0}

    errors = 0
    for row in rows:
        try:
            sb.table("practice_deadlines").update(
                {"status": "overdue"}
            ).eq("id", row["id"]).execute()

            practice_number = (row.get("practices") or {}).get("practice_number")
            kind = row["deadline_kind"]
            title = (row.get("metadata") or {}).get("title") or kind

            # Event log.
            record_event(
                tenant_id=row["tenant_id"],
                practice_id=row["practice_id"],
                document_id=row.get("document_id"),
                event_type=EVT_DEADLINE_BREACHED,
                payload={
                    "deadline_id": row["id"],
                    "deadline_kind": kind,
                    "due_at": row["due_at"],
                    "days_overdue": _days_between(row["due_at"], now),
                },
            )

            # Notification — best-effort, mirrors notifications_service
            # but kept inline to avoid the async wrapper.
            sb.table("notifications").insert(
                {
                    "tenant_id": row["tenant_id"],
                    "user_id": None,  # tenant-wide
                    "severity": "warning",
                    "title": f"Scadenza superata · {title}",
                    "body": (
                        f"La pratica {practice_number or row['practice_id']} "
                        f"ha una scadenza in ritardo: {title}."
                    ),
                    "href": f"/practices/{row['practice_id']}",
                    "metadata": {
                        "kind": "practice_deadline_breached",
                        "practice_id": row["practice_id"],
                        "practice_number": practice_number,
                        "deadline_id": row["id"],
                        "deadline_kind": kind,
                        "due_at": row["due_at"],
                    },
                }
            ).execute()
        except Exception:
            log.exception(
                "deadline.overdue_processing_failed",
                deadline_id=row.get("id"),
            )
            errors += 1

    log.info(
        "deadlines.cron_summary",
        newly_overdue=len(rows) - errors,
        errors=errors,
    )
    return {"newly_overdue": len(rows) - errors, "errors": errors}


# ---------------------------------------------------------------------------
# Read API — used by the dashboard scadenze panel and the practice
# detail timeline.
# ---------------------------------------------------------------------------


def list_deadlines_for_practice(
    *, tenant_id: str | UUID, practice_id: str | UUID
) -> list[dict[str, Any]]:
    sb = get_service_client()
    res = (
        sb.table("practice_deadlines")
        .select("*")
        .eq("tenant_id", str(tenant_id))
        .eq("practice_id", str(practice_id))
        .order("due_at", desc=False)
        .execute()
    )
    return res.data or []


def list_open_deadlines_for_tenant(
    *, tenant_id: str | UUID, limit: int = 100
) -> list[dict[str, Any]]:
    """Tenant-wide open + overdue deadlines, ordered by urgency."""
    sb = get_service_client()
    res = (
        sb.table("practice_deadlines")
        .select("*, practices(practice_number, status, lead_id)")
        .eq("tenant_id", str(tenant_id))
        .in_("status", ["open", "overdue"])
        .order("due_at", desc=False)
        .limit(limit)
        .execute()
    )
    return res.data or []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> datetime:
    """Parse a Postgres-formatted timestamp.  Postgres emits ISO 8601
    with a `+00:00` offset; ``datetime.fromisoformat`` handles that on
    Python ≥ 3.11.  Fallback to a Z-suffix parse for older formats."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        # Last-resort: treat as UTC noon to avoid TZ surprises.
        return datetime.now(timezone.utc)


def _days_between(iso_value: str, now: datetime) -> int:
    return max(0, (now - _parse_iso(iso_value)).days)
