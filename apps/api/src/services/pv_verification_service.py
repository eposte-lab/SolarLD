"""Existing-PV re-verification — the recovery half of the fail-closed gate.

The funnel (L4/L6) and the outreach send-guard park any lead whose roof could
NOT be CONFIDENTLY verified panel-free in ``pipeline_status='pending_pv_check'``
(plus a ``reverification_queue`` row with ``reason='pv_unverified'``). Without a
recovery path those leads would be stuck forever and lead supply would collapse,
so this cron re-runs satellite vision on the held roofs and resolves each:

  - confident "no panels"  → RELEASE: pipeline_status='ready_to_send' (+ render)
  - confident "has panels" → REJECT:  blacklist + has_existing_pv=true
  - still not confident     → retry next run; after ``MAX_PV_REVERIFY_AGE_HOURS``
                              escalate to OPERATOR REVIEW (a human confirms the
                              ambiguous roofs vision can't settle — e.g. Hotel
                              Olimpico). The lead stays held (never sent) until
                              resolved either way.

Invariant: a lead only ever leaves ``pending_pv_check`` to ``ready_to_send`` via
a CONFIDENT "no panels" verdict — so nothing unverified is ever sent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..core.logging import get_logger
from ..core.queue import enqueue
from ..core.supabase_client import get_service_client
from .claude_vision_service import verify_existing_pv

log = get_logger(__name__)

# Held status set by the funnel + send-guard for roofs we couldn't confirm clean.
PENDING_PV_STATUS = "pending_pv_check"

# Per cron tick: each lead costs one ~0.5¢ Mapbox+vision call, so bound the spend
# and let the next tick mop up the rest.
PER_RUN_CAP = 20

# After this long held without a confident verdict, stop auto-retrying and route
# to operator review — vision will likely never settle this roof on its own.
MAX_PV_REVERIFY_AGE_HOURS = 48


def roof_pv_verified_clean(roof: dict[str, Any] | None) -> bool:
    """True only when a roof dict carries a CONFIDENT "no panels" verdict.

    Verified-clean = ``existing_pv_checked_at IS NOT NULL AND has_existing_pv =
    false``. Pure helper shared by the send-guard and tests.
    """
    if not roof:
        return False
    return bool(roof.get("existing_pv_checked_at")) and not roof.get("has_existing_pv")


def enqueue_pv_reverification(sb: Any, tenant_id: str, lead_id: str) -> None:
    """Idempotently park a lead in ``reverification_queue`` for a PV re-check.

    Keyed on (tenant_id, lead_id); ``reason='pv_unverified'`` distinguishes these
    from the warehouse-expiry rows. Best-effort: the cron also scans
    ``pending_pv_check`` leads directly, so a missed enqueue is not fatal.
    """
    try:
        sb.table("reverification_queue").upsert(
            {"tenant_id": tenant_id, "lead_id": lead_id, "reason": "pv_unverified"},
            on_conflict="tenant_id,lead_id",
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("pv_reverify.enqueue_failed", lead_id=lead_id, err=str(exc)[:160])


def _one_roof(value: Any) -> dict[str, Any]:
    """PostgREST embeds can return a dict or a single-element list."""
    if isinstance(value, list):
        return value[0] if value else {}
    return value or {}


def lead_roof_sendable(sb: Any, lead_id: str) -> tuple[bool, str]:
    """Whether a lead may be emailed, by its roof's existing-PV verdict.

    For OPERATOR-manual send paths that bypass ``OutreachAgent`` (drafted
    follow-ups). Loads the lead's roof itself so callers don't need to widen
    their SELECT. Returns ``(ok, reason)``; ``reason`` is one of
    ``has_existing_pv`` / ``pv_unverified`` / ``pv_lookup_error`` when blocked.
    Fails CLOSED: a lookup error blocks the send.
    """
    try:
        res = (
            sb.table("leads")
            .select("roof_id, roofs(has_existing_pv, existing_pv_checked_at)")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        roof = _one_roof((res.data or [{}])[0].get("roofs"))
    except Exception as exc:  # noqa: BLE001 — fail closed
        log.warning("pv_sendable.lookup_failed", lead_id=lead_id, err=str(exc)[:160])
        return False, "pv_lookup_error"
    if roof.get("has_existing_pv"):
        return False, "has_existing_pv"
    if not roof.get("existing_pv_checked_at"):
        return False, "pv_unverified"
    return True, ""


def _age_hours(lead: dict[str, Any]) -> float:
    stamp = lead.get("last_status_transition_at") or lead.get("created_at")
    if not stamp:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        return (datetime.now(UTC) - dt).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return 0.0


def _resolve_queue(sb: Any, tenant_id: str, lead_id: str, *, outcome: str, notes: str = "") -> None:
    try:
        sb.table("reverification_queue").update(
            {
                "attempted_at": datetime.now(UTC).isoformat(),
                "resolved_at": datetime.now(UTC).isoformat(),
                "outcome": outcome,
                "notes": notes or None,
            }
        ).eq("tenant_id", tenant_id).eq("lead_id", lead_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.debug("pv_reverify.queue_resolve_failed", lead_id=lead_id, err=str(exc)[:120])


def _emit(sb: Any, tenant_id: str, lead_id: str, event_type: str, payload: dict[str, Any]) -> None:
    try:
        sb.table("events").insert(
            {
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "event_type": event_type,
                "event_source": "pv_reverify",
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": payload,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.debug("pv_reverify.event_failed", lead_id=lead_id, err=str(exc)[:120])


async def run_pv_reverification(*, limit: int = PER_RUN_CAP) -> dict[str, int]:
    """Re-verify a batch of PV-held leads. Returns per-outcome counts."""
    sb = get_service_client()
    res = (
        sb.table("leads")
        .select(
            "id, tenant_id, roof_id, created_at, last_status_transition_at, "
            "roofs(id, lat, lng, area_sqm, has_existing_pv, existing_pv_checked_at)"
        )
        .eq("pipeline_status", PENDING_PV_STATUS)
        .order("last_status_transition_at", desc=False)
        .limit(limit)
        .execute()
    )
    leads = res.data or []
    released = rejected = still_pending = escalated = errored = 0

    for lead in leads:
        lead_id = lead["id"]
        tenant_id = lead["tenant_id"]
        roof = _one_roof(lead.get("roofs"))
        lat, lng = roof.get("lat"), roof.get("lng")

        if lat is None or lng is None:
            # No coordinates to verify against → a human must decide.
            _escalate(sb, tenant_id, lead_id, reason="no_coords")
            escalated += 1
            continue

        try:
            verdict = await verify_existing_pv(
                float(lat), float(lng), area_sqm=roof.get("area_sqm")
            )
        except Exception as exc:  # noqa: BLE001 — vision/network hiccup → retry next tick
            log.warning("pv_reverify.vision_error", lead_id=lead_id, err=str(exc)[:160])
            errored += 1
            continue

        if verdict.checked:
            try:
                sb.table("roofs").update(
                    {
                        "has_existing_pv": verdict.has_pv,
                        "existing_pv_confidence": verdict.confidence,
                        "existing_pv_checked_at": datetime.now(UTC).isoformat(),
                    }
                ).eq("id", roof.get("id")).execute()
            except Exception as exc:  # noqa: BLE001
                log.warning("pv_reverify.roof_update_failed", lead_id=lead_id, err=str(exc)[:160])
                errored += 1
                continue

            if verdict.has_pv:
                # Confirmed panels → reject, mirroring admin_exclude_lead.
                sb.table("leads").update(
                    {
                        "pipeline_status": "blacklisted",
                        "operator_released_at": None,
                        "operator_review_status": "held",
                        "engagement_score": 0,
                    }
                ).eq("id", lead_id).execute()
                _resolve_queue(
                    sb, tenant_id, lead_id, outcome="blacklisted", notes="has_existing_pv"
                )
                _emit(
                    sb, tenant_id, lead_id, "lead.pv_rejected", {"confidence": verdict.confidence}
                )
                rejected += 1
            else:
                # Verified clean → release into the warehouse + ensure rendered.
                sb.table("leads").update({"pipeline_status": "ready_to_send"}).eq(
                    "id", lead_id
                ).execute()
                _resolve_queue(sb, tenant_id, lead_id, outcome="requeued", notes="verified_clean")
                _emit(
                    sb,
                    tenant_id,
                    lead_id,
                    "lead.pv_verified_clean",
                    {"confidence": verdict.confidence},
                )
                try:
                    await enqueue(
                        "creative_task",
                        {"tenant_id": tenant_id, "lead_id": lead_id},
                        job_id=f"creative:{tenant_id}:{lead_id}",
                    )
                except Exception:  # noqa: BLE001 — render enqueue best-effort
                    pass
                released += 1
        else:
            # Still not confident. Escalate if it's been held too long.
            if _age_hours(lead) >= MAX_PV_REVERIFY_AGE_HOURS:
                _escalate(sb, tenant_id, lead_id, reason="vision_inconclusive")
                escalated += 1
            else:
                still_pending += 1

    result = {
        "scanned": len(leads),
        "released": released,
        "rejected": rejected,
        "still_pending": still_pending,
        "escalated": escalated,
        "errored": errored,
    }
    if leads:
        log.info("pv_reverify.done", **result)
    return result


def _escalate(sb: Any, tenant_id: str, lead_id: str, *, reason: str) -> None:
    """Stop auto-retrying and route an ambiguous roof to operator review.

    The lead stays in ``pending_pv_check`` (held, never sent); we flag it for the
    operator (``operator_review_status='held'``) and emit an event so it surfaces
    for a human to confirm whether the roof has panels.
    """
    try:
        sb.table("leads").update({"operator_review_status": "held"}).eq("id", lead_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.debug("pv_reverify.escalate_update_failed", lead_id=lead_id, err=str(exc)[:120])
    _resolve_queue(sb, tenant_id, lead_id, outcome="operator_review", notes=reason)
    _emit(sb, tenant_id, lead_id, "lead.pv_needs_operator", {"reason": reason})
