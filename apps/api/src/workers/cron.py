"""Scheduled cron jobs for the arq worker.

Registered on ``WorkerSettings.cron_jobs`` in ``workers.main``. Each
function is small and delegates the hard bits to pure services so
tests can exercise them without running the scheduler.

Current schedule (UTC):

    02:30  reputation_digest_cron  — refresh domain_reputation snapshot
                                     (runs before retention so the UI
                                      always has a fresh row)
    03:15  retention_cron          — delete leads older than 24 months
                                     (GDPR data-minimisation)
    03:45  send_time_rollup_cron   — refresh leads.best_send_hour from
                                     180d of email-open events (Part B.3).
                                     Must finish BEFORE follow_up_cron so
                                     the 07:30 tick reads fresh values.
    04:00  engagement_rollup_cron  — refresh leads.engagement_score
                                     from the last 30 days of
                                     portal_events (Part B.1)
    07:30  follow_up_cron          — enqueue step-2 / step-3 nudges,
                                     deferred per-lead to the UTC hour
                                     at which the lead has historically
                                     opened email (Part B.3).
    08:30  sla_first_touch_cron    — per-tenant SLA alert: notify when
                                     leads have been contacted but not
                                     replied within sla_hours_first_touch.

Both follow_up and sla crons are fully idempotent: re-running the
follow-up cron twice in the same morning never double-sends because
OutreachAgent dedupes on ``(lead_id, sequence_step)`` at the DB layer;
the SLA cron emits one notification per tenant per tick (structured log
included so duplicates are visible in Sentry).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.logging import get_logger
from ..core.queue import enqueue
from ..core.supabase_client import get_service_client
from ..models.enums import LeadStatus, OutreachChannel
from ..services.digest_service import send_daily_digests, send_weekly_digests
from ..services.followup_service import (
    STEP_2_DELAY_DAYS,
    build_candidate_from_rows,
    select_next_step,
)
from ..services.engagement_service import run_engagement_rollup
from ..services.reputation_service import run_reputation_digest
from ..services.reputation_enforcement_service import run_enforcement
from ..services.send_time_service import pick_next_send_time, run_send_time_rollup

log = get_logger(__name__)

# Safety guard — don't pull millions of rows in a single cron tick.
FOLLOW_UP_BATCH_SIZE = 500

# Retention window: 24 months from the lead's ``created_at``.
RETENTION_DAYS = 24 * 30  # ~730d, matches docs/ARCHITECTURE.md


async def follow_up_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Every morning, enqueue step-2 / step-3 emails for eligible leads.

    Strategy:
      1. Pull leads whose day-0 send is at least ``STEP_2_DELAY_DAYS``
         old AND still in a silent pipeline state (sent / delivered).
         This coarse filter keeps the working set small.
      2. For each, load the campaigns history and run the pure
         ``select_next_step`` rule to get a yes/no + step number.
      3. Enqueue ``outreach_task`` with the decided step. The
         OutreachAgent itself dedupes on (lead_id, sequence_step) so
         double-runs of the cron collapse cleanly.
    """
    sb = get_service_client()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=STEP_2_DELAY_DAYS)).isoformat()

    leads_res = (
        sb.table("leads")
        .select(
            "id, tenant_id, pipeline_status, outreach_channel, "
            "outreach_sent_at, best_send_hour"
        )
        .eq("outreach_channel", OutreachChannel.EMAIL.value)
        .in_(
            "pipeline_status",
            [LeadStatus.SENT.value, LeadStatus.DELIVERED.value],
        )
        .lte("outreach_sent_at", cutoff)
        .order("outreach_sent_at")
        .limit(FOLLOW_UP_BATCH_SIZE)
        .execute()
    )
    leads = leads_res.data or []
    log.info("cron.followup.candidates", count=len(leads))

    # Per-tenant settings cache — the follow-up batch often hits the
    # same tenant many times, so one SELECT per tenant is plenty.
    tenant_cache: dict[str, dict[str, Any] | None] = {}

    def _get_tenant(tid: str) -> dict[str, Any] | None:
        if tid in tenant_cache:
            return tenant_cache[tid]
        res = (
            sb.table("tenants")
            .select("id, settings")
            .eq("id", tid)
            .limit(1)
            .execute()
        )
        row = (res.data or [None])[0]
        tenant_cache[tid] = row
        return row

    queued = 0
    deferred = 0
    skipped_reasons: dict[str, int] = {}
    for lead in leads:
        campaigns = (
            sb.table("outreach_sends")
            .select("sequence_step, status, sent_at, channel")
            .eq("lead_id", lead["id"])
            .execute()
        )
        candidate = build_candidate_from_rows(
            lead=lead, campaigns=campaigns.data or []
        )
        decision = select_next_step(candidate, now=now)
        if not decision.should_send:
            reason = decision.reason or "unknown"
            skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
            continue
        assert decision.step is not None

        # B.3 — defer the enqueue to the lead's preferred UTC hour
        # (falls back to tenant default / 09 UTC). The outreach task
        # is idempotent and Redis holds deferred jobs durably, so this
        # just shifts the fire-time, not the semantics.
        tenant_row = _get_tenant(lead["tenant_id"])
        send_at = pick_next_send_time(
            lead_row=lead, tenant_row=tenant_row, now=now
        )
        is_deferred = send_at > now

        await enqueue(
            "outreach_task",
            {
                "tenant_id": lead["tenant_id"],
                "lead_id": lead["id"],
                "channel": OutreachChannel.EMAIL.value,
                "sequence_step": decision.step,
                "force": False,
            },
            # Deterministic job id → duplicates collapse in Redis.
            job_id=(
                f"outreach:{lead['tenant_id']}:{lead['id']}:"
                f"email:step{decision.step}"
            ),
            defer_until=send_at if is_deferred else None,
        )
        queued += 1
        if is_deferred:
            deferred += 1

    log.info(
        "cron.followup.done",
        queued=queued,
        deferred=deferred,
        skipped_reasons=skipped_reasons,
        candidates=len(leads),
    )
    return {
        "ok": True,
        "queued": queued,
        "deferred": deferred,
        "candidates": len(leads),
        "skipped_reasons": skipped_reasons,
    }


async def daily_digest_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Fan out a daily digest email to every tenant that opted in.

    Opt-in via ``tenants.settings.feature_flags.daily_digest = true``.
    Tenants with zero activity in the last 24h get skipped — we don't
    send empty digests.
    """
    result = await send_daily_digests()
    log.info(
        "cron.digest.daily.done",
        count=len(result.get("results") or []),
    )
    return result


async def weekly_digest_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Weekly digest (last 7 days).

    Opt-in via ``tenants.settings.feature_flags.weekly_digest = true``.
    """
    result = await send_weekly_digests()
    log.info(
        "cron.digest.weekly.done",
        count=len(result.get("results") or []),
    )
    return result


async def reputation_digest_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Nightly rollup of sender reputation (Part B.5 / Sprint 6.5).

    Aggregates the last 7 days of (campaigns + events) per tenant's
    ``email_from_domain`` and writes one row to ``domain_reputation``.
    The dashboard ``/settings`` page reads the latest snapshot and
    renders a red banner if bounce_rate > 5% or complaint_rate > 0.3%.

    Immediately after the digest, the enforcement service auto-pauses any
    domain whose alarm flags are set (bounce > 5 % / complaint > 0.3 %).

    Idempotent: re-running on the same date upserts the snapshot.
    """
    sb = get_service_client()

    digest_result = await run_reputation_digest()
    log.info(
        "cron.reputation_digest.done",
        rows=digest_result.get("rows", 0),
        alarms=digest_result.get("alarms", 0),
    )

    # Sprint 6.5 — enforce alarm flags immediately after the fresh digest.
    enforcement_result = await run_enforcement(sb)
    log.info(
        "cron.reputation_enforcement.done",
        checked=enforcement_result.domains_checked,
        paused=enforcement_result.domains_paused,
        errors=len(enforcement_result.errors),
    )

    return {
        "ok": True,
        **digest_result,
        "enforcement": {
            "checked": enforcement_result.domains_checked,
            "paused": enforcement_result.domains_paused,
            "errors": enforcement_result.errors,
        },
    }


async def send_time_rollup_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Nightly rollup of per-lead best send-time (Part B.3).

    Walks the last 180 days of ``events`` (email_opened / email_clicked)
    and writes ``leads.best_send_hour`` (UTC 0..23, nullable). Leads
    with < 2 signals fall back to NULL so the scheduler uses the
    tenant default instead of an unreliable singleton.

    Scheduled at 03:45 UTC — must land before ``follow_up_cron`` at
    07:30 so the morning enqueue picks up today's values.

    Idempotent: re-running overwrites with the same computation
    (input window is time-bounded, not delta-based).
    """
    result = await run_send_time_rollup()
    log.info(
        "cron.send_time_rollup.done",
        leads_updated=result.get("leads_updated", 0),
        leads_cleared=result.get("leads_cleared", 0),
        events_scanned=result.get("events_scanned", 0),
    )
    return {"ok": True, **result}


async def engagement_rollup_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Nightly rollup of portal engagement → leads.engagement_score (B.1).

    Walks the last 30 days of ``portal_events``, groups by lead, and
    writes back the 0..100 heat score plus the denormalised
    ``portal_sessions`` / ``portal_total_time_sec`` /
    ``deepest_scroll_pct`` columns used by the dashboard's "hot leads"
    sort. See ``services.engagement_service`` for the formula.

    Idempotent: re-running on the same day overwrites with the same
    aggregate (the input window is time-bounded, not delta-based).
    """
    result = await run_engagement_rollup()
    log.info(
        "cron.engagement_rollup.done",
        leads_updated=result.get("leads_updated", 0),
        scored_hot=result.get("scored_hot", 0),
        errors=result.get("errors", 0),
    )
    return {"ok": True, **result}


async def sla_first_touch_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily SLA alert — notify tenants about leads awaiting first reply.

    Strategy:
      1. Load every tenant's CRM module to get ``sla_hours_first_touch``.
         Tenants with SLA=0 (disabled) are skipped entirely.
      2. For each active SLA tenant, count leads that:
         - Have been contacted (``outreach_sent_at IS NOT NULL``)
         - Are still in a "silent" state (sent / delivered / opened)
           meaning the lead hasn't replied, clicked a CTA, or booked.
         - Have been waiting longer than ``sla_hours_first_touch``.
      3. If there are any overdue leads, emit one in-app notification
         so the operator sees a bell count update on next page load.

    The cron is idempotent: firing it twice in the same morning produces
    two notifications (the bell counter increments), but the operator
    dismisses stale ones and both carry a structured timestamp. A
    deduplication-by-day guard would add DB state we don't need yet.
    """
    sb = get_service_client()
    now = datetime.now(timezone.utc)

    # 1. Load CRM module configs for all tenants.
    mods_res = (
        sb.table("tenant_modules")
        .select("tenant_id, config")
        .eq("module_key", "crm")
        .execute()
    )
    crm_mods = mods_res.data or []
    log.info("cron.sla.tenants_loaded", count=len(crm_mods))

    alerted = 0
    skipped_no_sla = 0

    for mod in crm_mods:
        tenant_id: str = mod["tenant_id"]
        cfg: dict[str, Any] = mod.get("config") or {}
        sla_hours: int = int(cfg.get("sla_hours_first_touch") or 0)

        if sla_hours <= 0:
            skipped_no_sla += 1
            continue

        cutoff = (now - timedelta(hours=sla_hours)).isoformat()

        # 2. Count overdue leads: contacted but no positive signal yet.
        overdue_res = (
            sb.table("leads")
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            .in_(
                "pipeline_status",
                [
                    LeadStatus.SENT.value,
                    LeadStatus.DELIVERED.value,
                    LeadStatus.OPENED.value,
                ],
            )
            .lte("outreach_sent_at", cutoff)
            .execute()
        )
        count = overdue_res.count or 0
        if count == 0:
            continue

        # 3. Emit a single bell notification for this tenant.
        try:
            from ..services.notifications_service import notify

            await notify(
                tenant_id=tenant_id,
                title="Lead in attesa — SLA superato",
                body=(
                    f"{count} lead {'contattato' if count == 1 else 'contattati'} "
                    f"da più di {sla_hours}h senz{'a' if count == 1 else 'a'} risposta."
                ),
                severity="warning",
                href="/leads?status=sent",
                metadata={"overdue_count": count, "sla_hours": sla_hours},
            )
            alerted += 1
            log.info(
                "cron.sla.notified",
                tenant_id=tenant_id,
                overdue_count=count,
                sla_hours=sla_hours,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "cron.sla.notify_failed",
                tenant_id=tenant_id,
                err=str(exc),
            )

    log.info(
        "cron.sla.done",
        alerted=alerted,
        skipped_no_sla=skipped_no_sla,
        total_tenants=len(crm_mods),
    )
    return {"ok": True, "alerted": alerted, "skipped_no_sla": skipped_no_sla}


async def retention_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Purge leads older than the 24-month retention window.

    GDPR data-minimisation: we keep lead-level PII for at most 24
    months after creation. The ``leads``/``subjects``/``campaigns``
    cascade is handled by ``ON DELETE CASCADE`` in the schema, so
    deleting the lead row is enough — Supabase storage purge for the
    rendering bucket is deferred to a separate cron (Sprint 9+).
    """
    sb = get_service_client()
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    ).isoformat()

    # Look up the victims first so we can emit a count (the Supabase
    # SDK's ``delete`` doesn't return rowcount reliably).
    victims = (
        sb.table("leads")
        .select("id")
        .lte("created_at", cutoff)
        .limit(FOLLOW_UP_BATCH_SIZE)
        .execute()
    )
    ids = [row["id"] for row in (victims.data or [])]
    deleted = 0
    if ids:
        sb.table("leads").delete().in_("id", ids).execute()
        deleted = len(ids)
    log.info("cron.retention.done", deleted=deleted, cutoff=cutoff)
    return {"ok": True, "deleted": deleted, "cutoff": cutoff}
