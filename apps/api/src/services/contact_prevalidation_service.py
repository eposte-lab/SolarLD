"""Contact pre-validation — qualify already-ready leads' emails BEFORE the send.

The send-time gate (``OutreachAgent``) NeverBounce-checks each address at the
moment of sending. But a lead can reach the warehouse (``ready_to_send`` /
``picked`` / ``rendered`` …) having NEVER been validated — e.g. it was promoted
before contact-qualification existed, or the funnel didn't run the check. Those
leads then burn a daily-cap slot each when the send finally discovers the
address is dead (and, if NeverBounce is down at that moment, risk an
un-validated send).

This service pre-validates the sendable backlog so the warehouse only ever holds
contactable addresses. It mirrors :mod:`pv_verification_service` (the existing-PV
recovery cron): same ``get_service_client`` + event-emit + per-run-cap shape.

Policy mirrors the send-time gate exactly (outreach.py): only **INVALID** and
**DISPOSABLE** verdicts are hard-excluded (→ ``blacklisted``); **UNKNOWN** is
left sendable (fail-open, matching ``settings.outreach_send_to_unknown_email``);
**VALID / CATCHALL** stay sendable. So nothing this service does is stricter than
what the send would do anyway — it just does it earlier and removes the dead
weight up front.

Each NeverBounce call is logged to ``api_usage_log`` (provider='neverbounce',
``metadata.lead_id``) exactly like ``_check_neverbounce``; that doubles as the
"already pre-validated" marker, so the cron skips leads it has already seen — no
new column / migration needed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from .neverbounce_service import (
    NEVERBOUNCE_COST_PER_CALL_CENTS,
    VerificationResult,
    verify_email,
)

log = get_logger(__name__)

# Warehouse / pre-send states whose leads will be EMAILED and therefore must
# carry a validated contact. ``to_call`` is a phone disposition (excluded — an
# invalid email there must not kill a phone lead); ``engaged``/``sent`` are past
# the first send.
SENDABLE_STATES: tuple[str, ...] = (
    "ready_to_send",
    "picked",
    "qualified",
    "rendering",
    "rendered",
)

# Per tick each lead is one ~1¢ NeverBounce call. Bound the spend; the next tick
# mops up the rest.
PER_RUN_CAP = 100

# Hard-exclude ONLY confirmed-bad verdicts — identical to the send-time gate.
_EXCLUDE_RESULTS = {VerificationResult.INVALID, VerificationResult.DISPOSABLE}


def _one(value: Any) -> dict[str, Any]:
    """PostgREST embeds can be a dict or a single-element list."""
    if isinstance(value, list):
        return value[0] if value else {}
    return value or {}


def _log_usage(sb: Any, tenant_id: str, email: str, result_value: str, lead_id: str) -> None:
    """Mirror ``outreach._check_neverbounce`` so the lead counts as validated."""
    try:
        sb.table("api_usage_log").insert(
            {
                "tenant_id": tenant_id,
                "provider": "neverbounce",
                "endpoint": "single/check",
                "request_count": 1,
                "cost_cents": NEVERBOUNCE_COST_PER_CALL_CENTS,
                "status": "success",
                "metadata": {
                    "email_domain": email.split("@", 1)[1] if "@" in email else "",
                    "result": result_value,
                    "lead_id": lead_id,
                    "source": "prevalidation",
                },
            }
        ).execute()
    except Exception:  # noqa: BLE001 — analytics only, never block
        pass


def _emit(sb: Any, tenant_id: str, lead_id: str, event_type: str, payload: dict[str, Any]) -> None:
    try:
        sb.table("events").insert(
            {
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "event_type": event_type,
                "event_source": "contact_prevalidate",
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": payload,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.debug("contact_prevalidate.event_failed", lead_id=lead_id, err=str(exc)[:120])


async def run_contact_prevalidation(
    *,
    tenant_id: str | None = None,
    lead_ids: list[str] | None = None,
    limit: int = PER_RUN_CAP,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Pre-validate the sendable backlog's contacts. Returns per-outcome counts.

    ``lead_ids`` validates exactly those leads (backfill of a known set) and
    skips the "already validated" de-dup. Otherwise it self-selects the
    never-sent leads in :data:`SENDABLE_STATES` that have an email and have not
    yet been NeverBounce-logged (the cron path). ``dry_run`` calls NeverBounce
    and reports, but writes nothing (no usage log, no exclusion).
    """
    sb = get_service_client()

    # 1. Candidate leads.
    q = sb.table("leads").select(
        "id, tenant_id, pipeline_status, outreach_sent_at, "
        "subjects(business_name, decision_maker_email)"
    )
    if lead_ids:
        q = q.in_("id", lead_ids)
    else:
        q = q.in_("pipeline_status", list(SENDABLE_STATES)).is_("outreach_sent_at", "null")
        if tenant_id:
            q = q.eq("tenant_id", tenant_id)
        q = q.order("created_at", desc=False).limit(max(limit * 4, limit))
    rows = (q.execute().data) or []

    # 2. Build the work-list: never-sent leads that actually have an email.
    work: list[tuple[str, str, str, str]] = []  # (lead_id, tenant_id, email, business_name)
    for r in rows:
        if r.get("outreach_sent_at"):
            continue
        subj = _one(r.get("subjects"))
        email = (subj.get("decision_maker_email") or "").strip()
        if not email or "@" not in email:
            continue
        work.append((r["id"], r["tenant_id"], email, subj.get("business_name") or ""))

    # 3. De-dup against prior NeverBounce logs (cost guard) — only for the
    #    self-select/cron path; an explicit ``lead_ids`` backfill is intentional.
    if work and not lead_ids:
        ids = [w[0] for w in work]
        try:
            logged = (
                sb.table("api_usage_log")
                .select("metadata")
                .eq("provider", "neverbounce")
                .filter("metadata->>lead_id", "in", f"({','.join(ids)})")
                .execute()
            ).data or []
            done = {(row.get("metadata") or {}).get("lead_id") for row in logged}
            work = [w for w in work if w[0] not in done]
        except Exception as exc:  # noqa: BLE001 — re-validating is harmless, just costs 1¢
            log.debug("contact_prevalidate.dedup_failed", err=str(exc)[:120])

    work = work[:limit]

    counts: dict[str, Any] = {
        "scanned": len(work),
        "valid": 0,
        "catchall": 0,
        "unknown": 0,
        "excluded_invalid": 0,
        "excluded_disposable": 0,
        "errored": 0,
    }
    excluded: list[dict[str, str]] = []

    for lead_id, t_id, email, business in work:
        try:
            verdict = await verify_email(email)
        except Exception as exc:  # noqa: BLE001 — NeverBounce hiccup → retry next tick
            log.warning("contact_prevalidate.nb_error", lead_id=lead_id, err=str(exc)[:160])
            counts["errored"] += 1
            continue

        if not dry_run:
            _log_usage(sb, t_id, email, verdict.result.value, lead_id)

        res = verdict.result
        if res in _EXCLUDE_RESULTS:
            key = "excluded_invalid" if res is VerificationResult.INVALID else "excluded_disposable"
            counts[key] += 1
            excluded.append(
                {"business": business, "domain": email.split("@", 1)[1], "verdict": res.value}
            )
            if not dry_run:
                sb.table("leads").update(
                    {"pipeline_status": "blacklisted", "engagement_score": 0}
                ).eq("id", lead_id).execute()
                _emit(
                    sb,
                    t_id,
                    lead_id,
                    "lead.contact_invalid",
                    {
                        "reason": f"neverbounce_{res.value}",
                        "email_domain": email.split("@", 1)[1],
                    },
                )
        elif res is VerificationResult.CATCHALL:
            counts["catchall"] += 1
        elif res is VerificationResult.VALID:
            counts["valid"] += 1
        else:  # UNKNOWN → fail-open, stays sendable (matches the send-time gate)
            counts["unknown"] += 1

    counts["excluded_detail"] = excluded
    if work:
        log.info(
            "contact_prevalidate.done",
            dry_run=dry_run,
            tenant_id=tenant_id,
            **{k: v for k, v in counts.items() if k != "excluded_detail"},
        )
    return counts
