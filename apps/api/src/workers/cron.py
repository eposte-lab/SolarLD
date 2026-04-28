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

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.logging import get_logger
from ..core.queue import enqueue
from ..core.supabase_client import get_service_client
from ..models.enums import LeadStatus, OutreachChannel
from ..services.digest_service import send_daily_digests, send_weekly_digests
from ..services.followup_service import (
    STEP_2_DELAY_DAYS,
    STEP_4_DELAY_DAYS,
    build_candidate_from_rows,
    select_next_step,
)
from ..services.followup_scenario_service import (
    FollowupSnapshot,
    evaluate_followup_scenario,
)
from ..services.notifications_service import notify as _notify_inapp
from ..services.deliverability_monitor_service import run_hourly_monitor
from ..services.engagement_service import run_engagement_rollup
from ..services.reputation_service import run_reputation_digest
from ..services.reputation_enforcement_service import run_enforcement
from ..services.send_time_service import pick_next_send_time, run_send_time_rollup
from ..core.config import settings

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


async def smartlead_warmup_sync_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily Smartlead warm-up sync — pull health scores + update inbox caps.

    Runs at 06:00 UTC, before the morning outreach pipeline (07:30), so
    ``inbox_service.pick_and_claim`` always has fresh warm-up phase data.

    For each active Gmail OAuth inbox across all tenants:
      1. Look up the Smartlead account ID (from ``tenant_inboxes.smartlead_account_id``).
      2. Fetch today's warm-up stats.
      3. Write ``warmup_started_at`` if it's the first sync (marks day 1 of ramp).
      4. Write ``smartlead_health_score`` so the dashboard can surface inbox health.

    Non-critical: errors per inbox are logged and skipped; a transient
    Smartlead outage does NOT block the pipeline. The effective daily cap
    is owned by ``rate_limit_service.inbox_effective_daily_cap`` which
    reads ``warmup_started_at`` — so even if the sync fails, the existing
    cap is used (slightly stale, never wrong in direction).

    Skipped entirely when ``SMARTLEAD_API_KEY`` is not configured (dev / testing).
    """
    if not settings.smartlead_api_key:
        log.debug("cron.smartlead_sync.skipped", reason="no_api_key")
        return {"ok": True, "skipped": True, "reason": "SMARTLEAD_API_KEY not set"}

    # Import here to keep cron.py import-time lightweight (no httpx import at top)
    from ..services.smartlead_service import (
        SmartleadError,
        get_all_smartlead_ids_for_tenant,
        sync_warmup_to_db,
    )

    sb = get_service_client()

    # Load all tenants that have at least one Gmail OAuth inbox.
    try:
        res = await asyncio.to_thread(
            lambda: sb.table("tenant_inboxes")
            .select("tenant_id")
            .eq("provider", "gmail_oauth")
            .eq("active", True)
            .execute()
        )
        tenant_ids: list[str] = list(
            {row["tenant_id"] for row in (res.data or []) if row.get("tenant_id")}
        )
    except Exception as exc:  # noqa: BLE001
        log.error("cron.smartlead_sync.tenant_load_failed", err=str(exc))
        return {"ok": False, "error": str(exc)}

    total_synced = 0
    total_failed = 0

    for tenant_id in tenant_ids:
        try:
            id_map = await get_all_smartlead_ids_for_tenant(
                tenant_id=tenant_id,
                sb=sb,
            )
            if not id_map:
                continue
            summary = await sync_warmup_to_db(
                tenant_id=tenant_id,
                sb=sb,
                email_to_smartlead_id=id_map,
            )
            synced = sum(1 for v in summary.values() if v.get("synced"))
            failed = sum(1 for v in summary.values() if not v.get("synced"))
            total_synced += synced
            total_failed += failed
            log.info(
                "cron.smartlead_sync.tenant_done",
                tenant_id=tenant_id,
                synced=synced,
                failed=failed,
            )
        except SmartleadError as exc:
            log.warning(
                "cron.smartlead_sync.tenant_skipped",
                tenant_id=tenant_id,
                err=str(exc),
            )
            total_failed += 1
        except Exception as exc:  # noqa: BLE001
            log.error(
                "cron.smartlead_sync.tenant_error",
                tenant_id=tenant_id,
                err=str(exc),
            )
            total_failed += 1

    log.info(
        "cron.smartlead_sync.done",
        tenants=len(tenant_ids),
        synced=total_synced,
        failed=total_failed,
    )
    return {"ok": True, "tenants": len(tenant_ids), "synced": total_synced, "failed": total_failed}


async def cluster_ab_evaluation_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily evaluation of cluster A/B tests — auto-promote winners (Sprint 9 Fase B.5).

    Runs at 03:30 UTC after the send_time and reputation rollups have
    written fresh data but before the morning follow-up dispatch.

    For every (tenant, cluster_signature) pair that has active A+B
    variants the worker:
      1. Aggregates sent/replied counts from outreach_sends over a 14-day
         rolling window.
      2. Updates the denormalised counters on cluster_copy_variants and
         appends a daily snapshot to ab_test_metrics_daily.
      3. Runs a chi-square 2×2 (Pearson + Yates, df=1) significance test:
         - p < 0.05 AND min_sent >= 100  → promote winner, new round
         - total_sent >= 1000 AND p >= 0.05 → no_difference, new round
    """
    sb = get_service_client()
    from ..services.cluster_ab_evaluator_service import evaluate_cluster_ab_tests

    result = await evaluate_cluster_ab_tests(sb)
    log.info("cron.cluster_ab_evaluation.done", **result)
    return {"ok": True, **result}


async def deliverability_hourly_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Task 15 — Hourly deliverability monitor.

    Scans all active outreach domains for short-window (4 h) bounce and
    complaint spikes.  If either threshold is exceeded the domain is
    auto-paused for 48 h via the same ``pause_domain_for_alarm`` logic
    used by the nightly enforcement pass.

    Schedule: runs at the top of every hour (:00) UTC.
    Complements the nightly ``reputation_digest_cron`` (02:30 UTC) which
    operates on a 7-day aggregate.

    Key thresholds (hourly / short-window — stricter than nightly):
      bounce rate    ≥ 5%   → pause 48 h
      complaint rate ≥ 0.1% → pause 48 h  (tighter than nightly 0.3%)

    Non-critical: domain-level errors are logged and skipped; pipeline
    continues normally.
    """
    sb = get_service_client()
    result = await run_hourly_monitor(sb)
    log.info(
        "cron.deliverability_hourly.done",
        checked=result.domains_checked,
        paused=result.domains_paused,
        low_volume_skipped=result.domains_skipped_low_volume,
        errors=len(result.errors),
    )
    if result.domains_paused:
        log.warning(
            "cron.deliverability_hourly.domains_paused",
            count=result.domains_paused,
            details=result.paused_details,
        )
    return {
        "ok": True,
        "checked": result.domains_checked,
        "paused": result.domains_paused,
        "errors": result.errors,
    }


# ---------------------------------------------------------------------------
# Sprint 10 — Engagement-based follow-up scenarios
# ---------------------------------------------------------------------------

# Map scenario → synthetic sequence_step value used in outreach_sends.
# Keeping these distinct from 1..4 means the existing per-step dedupe
# does not collide with the engagement engine, and reporting can group
# by step easily.
_SCENARIO_TO_STEP: dict[str, int] = {
    "cold": 5,
    "lukewarm": 6,
    "engaged": 7,
    "interessato": 8,
    "riattivazione": 9,
}

# Keep this aligned with FOLLOW_UP_BATCH_SIZE — we don't want one cron
# tick to enqueue thousands of jobs in a single transaction.
ENGAGEMENT_FOLLOWUP_BATCH = 500


async def engagement_followup_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily engagement-based follow-up dispatcher (Sprint 10).

    Runs at 08:15 UTC (after engagement_rollup at 04:00 has refreshed
    ``leads.engagement_score`` and after follow_up_cron at 07:30 has
    enqueued the cold-silence step 2/3/4 cadence).

    Strategy:
      1. Pull the working set: leads with at least one outreach send
         (so there's a relationship to follow up on) excluding leads in
         terminal pipeline states (won/lost/blacklisted).
      2. For each, build a FollowupSnapshot from existing columns.
      3. Decide a scenario via the pure ``evaluate_followup_scenario``.
      4. ``hot`` scenario → in-app notification (and audit-stamp via
         ``leads.hot_lead_alerted_at``). No email is sent.
      5. Email scenarios → enqueue ``outreach_task`` with
         ``engagement_scenario={scenario}`` and a synthetic
         sequence_step (5-9). The OutreachAgent renders the matching
         followup_{scenario}.j2 template and persists a row in
         ``followup_emails_sent`` after the send succeeds.

    Idempotent: per-scenario cooldowns are enforced by the pure
    decision module against ``last_followup_sent_at``. Re-running the
    cron the same day yields no duplicates.
    """
    sb = get_service_client()
    now = datetime.now(timezone.utc)
    cold_cutoff = (now - timedelta(days=STEP_4_DELAY_DAYS + 1)).isoformat()

    # Pull leads worth evaluating: have an initial outreach, not in
    # terminal states, and either currently engaged (score > 0) OR
    # cold-but-aged-out (eligible for cold scenario).
    terminal = [
        LeadStatus.CLOSED_WON.value,
        LeadStatus.CLOSED_LOST.value,
        LeadStatus.BLACKLISTED.value,
    ]
    leads_res = (
        sb.table("leads")
        .select(
            "id, tenant_id, pipeline_status, outreach_sent_at, "
            "engagement_score, engagement_peak_score, "
            "last_portal_event_at, last_followup_scenario, "
            "last_followup_sent_at, hot_lead_alerted_at"
        )
        .not_.is_("outreach_sent_at", "null")
        .not_.in_("pipeline_status", terminal)
        .order("engagement_score", desc=True)
        .limit(ENGAGEMENT_FOLLOWUP_BATCH)
        .execute()
    )
    leads = leads_res.data or []
    log.info("cron.engagement_followup.candidates", count=len(leads))

    queued = 0
    notified_hot = 0
    skipped: dict[str, int] = {}

    for lead in leads:
        # Detect cold-cadence completion: step 4 sent, OR initial send
        # is older than the breakup-day cutoff (sequence will never fire
        # step 4 anyway because the lead is silent).
        cold_complete = False
        outreach_sent_at = _parse_ts(lead.get("outreach_sent_at"))
        if outreach_sent_at is not None and outreach_sent_at.isoformat() <= cold_cutoff:
            step4 = (
                sb.table("outreach_sends")
                .select("id")
                .eq("lead_id", lead["id"])
                .eq("sequence_step", 4)
                .limit(1)
                .execute()
            )
            cold_complete = bool(step4.data) or (
                (now - outreach_sent_at).days >= STEP_4_DELAY_DAYS + 7
            )

        snap = FollowupSnapshot(
            lead_id=str(lead["id"]),
            tenant_id=str(lead["tenant_id"]),
            pipeline_status=str(lead.get("pipeline_status") or ""),
            engagement_score=int(lead.get("engagement_score") or 0),
            engagement_peak_score=int(
                lead.get("engagement_peak_score")
                or lead.get("engagement_score")
                or 0
            ),
            last_engagement_at=_parse_ts(lead.get("last_portal_event_at")),
            initial_outreach_at=outreach_sent_at,
            last_followup_scenario=lead.get("last_followup_scenario"),
            last_followup_sent_at=_parse_ts(lead.get("last_followup_sent_at")),
            hot_lead_alerted_at=_parse_ts(lead.get("hot_lead_alerted_at")),
            cold_sequence_complete=cold_complete,
        )

        decision = evaluate_followup_scenario(snap, now=now)
        if not decision.should_act:
            reason = decision.reason or "no_action"
            skipped[reason] = skipped.get(reason, 0) + 1
            continue

        if decision.notify_only:
            # Hot lead → notify operator, do NOT send email.
            await _notify_inapp(
                tenant_id=snap.tenant_id,
                title="Lead caldo: contatto manuale consigliato",
                body=(
                    f"Engagement score {snap.engagement_score}/100. "
                    "Il sistema ha sospeso le email automatiche — "
                    "prendi tu in mano il follow-up."
                ),
                severity="success",
                href=f"/leads/{snap.lead_id}",
                metadata={
                    "lead_id": snap.lead_id,
                    "engagement_score": snap.engagement_score,
                    "scenario": "hot",
                },
            )
            sb.table("leads").update(
                {"hot_lead_alerted_at": now.isoformat()}
            ).eq("id", snap.lead_id).execute()
            notified_hot += 1
            continue

        # Email scenario — enqueue an outreach_task. The OutreachAgent
        # branches on engagement_scenario and renders followup_{X}.j2.
        scenario = decision.scenario or "cold"
        step = _SCENARIO_TO_STEP.get(scenario, 5)
        await enqueue(
            "outreach_task",
            {
                "tenant_id": snap.tenant_id,
                "lead_id": snap.lead_id,
                "channel": OutreachChannel.EMAIL.value,
                "sequence_step": step,
                "engagement_scenario": scenario,
                "force": False,
            },
            job_id=(
                f"engagement_followup:{snap.tenant_id}:{snap.lead_id}:"
                f"{scenario}:{now.strftime('%Y%m%d')}"
            ),
        )
        queued += 1

    log.info(
        "cron.engagement_followup.done",
        queued=queued,
        notified_hot=notified_hot,
        candidates=len(leads),
        skipped=skipped,
    )
    return {
        "ok": True,
        "queued": queued,
        "notified_hot": notified_hot,
        "candidates": len(leads),
        "skipped": skipped,
    }


def _parse_ts(raw: Any) -> datetime | None:
    """Parse Supabase ISO timestamp strings defensively."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    s = str(raw).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
