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
    04:00  engagement_rollup_cron  — refresh leads.engagement_score
                                     from the last 30 days of
                                     portal_events (Part B.1)
    08:15  engagement_followup_cron— enqueue engagement-driven follow-ups
                                     (only for leads that showed interest).
    08:30  sla_first_touch_cron    — per-tenant SLA alert: notify when
                                     leads have been contacted but not
                                     replied within sla_hours_first_touch.

Follow-ups are engagement-gated: the old cold-silence cadence (fixed
day-4/9/14 nudges to silent leads) was removed — a lead that never
engages now receives no follow-up. The SLA cron emits one notification
per tenant per tick (structured log included so duplicates are visible
in Sentry).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from ..core.config import settings
from ..core.logging import get_logger
from ..core.queue import enqueue
from ..core.supabase_client import get_service_client
from ..models.enums import LeadStatus, OutreachChannel
from ..services.deliverability_monitor_service import run_hourly_monitor
from ..services.digest_service import send_daily_digests, send_weekly_digests
from ..services.engagement_service import run_engagement_rollup
from ..services.followup_scenario_service import (
    FollowupSnapshot,
    evaluate_followup_scenario,
)
from ..services.followup_service import STEP_4_DELAY_DAYS
from ..services.notifications_service import notify as _notify_inapp
from ..services.reputation_enforcement_service import run_enforcement
from ..services.reputation_service import run_reputation_digest
from ..services.send_time_service import run_send_time_rollup

log = get_logger(__name__)

# Safety guard — don't pull millions of rows in a single cron tick.
FOLLOW_UP_BATCH_SIZE = 500

# Retention window: 24 months from the lead's ``created_at``.
RETENTION_DAYS = 24 * 30  # ~730d, matches docs/ARCHITECTURE.md


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
    now = datetime.now(UTC)

    # 1. Load CRM module configs for all tenants.
    mods_res = (
        sb.table("tenant_modules").select("tenant_id, config").eq("module_key", "crm").execute()
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
    cutoff = (datetime.now(UTC) - timedelta(days=RETENTION_DAYS)).isoformat()

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
            lambda: (
                sb.table("tenant_inboxes")
                .select("tenant_id")
                .eq("provider", "gmail_oauth")
                .eq("active", True)
                .execute()
            )
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


async def warehouse_cleanup_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Sprint 11 — daily expiry sweep of the lead warehouse.

    Transitions every lead in ``ready_to_send`` past its ``expires_at``
    to ``expired`` and pushes it onto ``reverification_queue`` for an
    eventual fresh data pull. Bounded per tick (1000 rows) so a large
    backlog doesn't lock the table for minutes; the next tick mops up.
    """
    from ..services.warehouse_cleanup_service import expire_stale_warehouse_leads

    result = await expire_stale_warehouse_leads()
    log.info("cron.warehouse_cleanup.done", **result)
    return {"ok": True, **result}


async def daily_pipeline_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Sprint 11 — the per-tenant daily warehouse-pick orchestrator.

    Runs once per day for every active tenant: refills the warehouse
    if it dropped under ``warehouse_buffer_days`` of runway, then
    picks up to ``daily_target_send_cap`` leads in FIFO order via
    the atomic ``warehouse_pick`` RPC and enqueues a creative_task
    per picked lead (pick-time rendering — Solar+Kling don't fire
    until we've decided to send).
    """
    from ..services.daily_pipeline_orchestrator import run_daily_orchestrator

    result = await run_daily_orchestrator()
    log.info(
        "cron.daily_pipeline.done",
        tenants_processed=result.get("tenants_processed"),
        tenants_failed=result.get("tenants_failed"),
    )
    return {"ok": True, **{k: v for k, v in result.items() if k != "details"}}


async def scan_jobs_dispatcher_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Dispatcher per la coda scan_jobs (PR #refactor).

    Ogni ora al minuto 05:
      1. Reset midnight: ogni job con valid_leads_today_date < today
         viene resettato (valid_leads_today=0, valid_leads_today_date=today,
         status paused_daily_cap → in_progress).
      2. Per ogni tenant attivo, prende il PRIMO job da consumare
         (status IN ('pending','in_progress','paused_daily_cap'),
         ORDER BY priority ASC, LIMIT 1).
      3. Solo se valid_leads_today < daily_validated_cap, enqueue il
         worker hunter_funnel_v3_task con scan_job_id.

    Il worker stesso aggiornerà valid_leads_today/valid_leads_total e
    il suo status (paused_daily_cap quando raggiunto, exhausted quando
    territorio finito).
    """
    sb = get_service_client()
    today = datetime.now(tz=UTC).date()

    # ── 1. Reset daily counter ─────────────────────────────────────
    # `completed` (cap totale raggiunto) ed `exhausted` sono terminali:
    # non vanno rimessi in rotazione dal reset di mezzanotte.
    sb.table("scan_jobs").update(
        {
            "valid_leads_today": 0,
            "valid_leads_today_date": today.isoformat(),
        }
    ).neq("valid_leads_today_date", today.isoformat()).neq("status", "archived").neq(
        "status", "exhausted"
    ).neq("status", "completed").execute()

    # Promuovi paused_daily_cap → pending dopo il reset (il job torna
    # in coda; `in_progress` è riservato all'esecuzione effettiva).
    sb.table("scan_jobs").update({"status": "pending"}).eq("status", "paused_daily_cap").eq(
        "valid_leads_today_date", today.isoformat()
    ).eq("valid_leads_today", 0).execute()

    # ── 2. Per ogni tenant attivo, dispatch del job top-priority ────
    tenants_res = (
        sb.table("scan_jobs")
        .select("tenant_id")
        .in_("status", ["pending", "in_progress"])
        .execute()
    )
    tenant_ids = sorted({r["tenant_id"] for r in (tenants_res.data or [])})
    if not tenant_ids:
        return {"ok": True, "jobs_dispatched": 0}

    from ..core.queue import enqueue

    dispatched = 0
    for tid in tenant_ids:
        job_res = (
            sb.table("scan_jobs")
            .select("*")
            .eq("tenant_id", tid)
            .in_("status", ["pending", "in_progress"])
            .order("priority")
            .limit(1)
            .execute()
        )
        job = (job_res.data or [None])[0]
        if not job:
            continue

        # Hard cap: il job ha già raggiunto il limite quotidiano
        if (job.get("valid_leads_today") or 0) >= job["daily_validated_cap"]:
            sb.table("scan_jobs").update({"status": "paused_daily_cap"}).eq(
                "id", job["id"]
            ).execute()
            continue

        try:
            await enqueue(
                "hunter_funnel_v3_task",
                {
                    "tenant_id": tid,
                    "scan_job_id": job["id"],
                    "max_l1_candidates": job["daily_validated_cap"] * 5,
                },
                job_id=f"scan_job:{job['id']}:{int(datetime.now(tz=UTC).timestamp())}",
            )
            sb.table("scan_jobs").update(
                {
                    "status": "in_progress",
                    "last_run_at": datetime.now(tz=UTC).isoformat(),
                }
            ).eq("id", job["id"]).execute()
            dispatched += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "cron.scan_jobs.dispatch_failed",
                job_id=job["id"],
                err=str(exc)[:200],
            )
            sb.table("scan_jobs").update({"last_error": str(exc)[:500]}).eq(
                "id", job["id"]
            ).execute()

    log.info("cron.scan_jobs.dispatched", count=dispatched, tenants=len(tenant_ids))
    return {"ok": True, "jobs_dispatched": dispatched, "tenants": len(tenant_ids)}


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


# ---------------------------------------------------------------------------
# Weekly autonomous refresh — Phase 4
# ---------------------------------------------------------------------------

# Minimum age (days) of an active pair before we consider it "stuck"
# and worth refreshing. 30 days = the operator has had a month to
# accumulate samples; if chi-square didn't fire we assume volume is
# too thin to ever fire and we force fresh copy.
STALE_VARIANT_AGE_DAYS = 30

# Below this combined sent_count over the variant's lifetime we treat
# the cluster as "starved" — Haiku copy is unproven, refresh it. Above
# it the chi-square evaluator will decide on its own merit.
LOW_VOLUME_THRESHOLD = 100

# Hard cap per run to control Haiku spend. ~$0.001/cluster at Haiku 3.5
# pricing → 30 clusters * 4 weeks/month = 120 calls/month ≈ $0.12.
MAX_REFRESH_PER_RUN = 30


async def weekly_cluster_refresh_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Sunday 04:00 UTC — refresh stale low-volume A/B variant pairs.

    The chi-square evaluator only fires once a cluster has accumulated
    MIN_SAMPLES=100 sends per variant. For low-volume clusters
    (e.g. ATECO codes that match a handful of leads per month) the
    same Haiku output ends up running for months without any feedback
    loop. This cron breaks the deadlock:

      - Find every (tenant, cluster_signature) pair where the active
        variants are >= 30 days old AND total sent_count < 100.
      - For each: archive the existing pair (status → 'archived'),
        generate a NEW round via Haiku — using the older variant as
        the previous_winner baseline so the new round still benefits
        from whatever copy was already there.

    This is purely an "exploration kick" — no statistical decision is
    made. The next chi-square evaluation will then operate on fresh copy.

    Capped at MAX_REFRESH_PER_RUN clusters to bound spend. If more are
    eligible they roll over to next Sunday.
    """
    sb = get_service_client()

    # Find candidate clusters: active variant rows older than the threshold.
    cutoff = (datetime.now(UTC) - timedelta(days=STALE_VARIANT_AGE_DAYS)).isoformat()
    res = (
        sb.table("cluster_copy_variants")
        .select(
            "id, tenant_id, cluster_signature, round_number, variant_label, "
            "copy_subject, copy_opening_line, copy_proposition_line, "
            "cta_primary_label, sent_count, generated_at"
        )
        .eq("status", "active")
        .lt("generated_at", cutoff)
        .order("generated_at")
        .execute()
    )
    rows = res.data or []

    # Group by (tenant, cluster, round) — each pair has 2 rows (A + B).
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for r in rows:
        key = (r["tenant_id"], r["cluster_signature"], r["round_number"])
        grouped.setdefault(key, []).append(r)

    refreshed = 0
    skipped_high_volume = 0
    failed = 0

    for (tenant_id, cluster_sig, round_number), pair in grouped.items():
        if refreshed >= MAX_REFRESH_PER_RUN:
            break
        if len(pair) < 2:
            # Orphan row — skip to avoid corrupting the chi-square invariant.
            continue
        total_sent = sum(int(p.get("sent_count") or 0) for p in pair)
        if total_sent >= LOW_VOLUME_THRESHOLD:
            # The chi-square evaluator can handle this cluster on its own.
            skipped_high_volume += 1
            continue

        # Pick the variant with more replies (or just A) as the baseline.
        baseline = pair[0]
        previous_winner = {
            "copy_subject": baseline.get("copy_subject") or "",
            "copy_opening_line": baseline.get("copy_opening_line") or "",
            "copy_proposition_line": baseline.get("copy_proposition_line") or "",
            "cta_primary_label": baseline.get("cta_primary_label") or "",
        }

        try:
            # Archive the stale pair so the new round becomes the only
            # active one (the OutreachAgent picks active by max round_number).
            ids_to_archive = [p["id"] for p in pair]
            (
                sb.table("cluster_copy_variants")
                .update({"status": "archived"})
                .in_("id", ids_to_archive)
                .execute()
            )

            # Fetch tenant name for prompt personalisation.
            tn_resp = (
                sb.table("tenants").select("business_name").eq("id", tenant_id).single().execute()
            )
            tenant_name = (tn_resp.data or {}).get("business_name") or "SolarLead"

            from ..services.variant_generator_service import (
                generate_variant_pair,
                persist_variant_pair,
            )

            va, vb = await generate_variant_pair(
                tenant_name=tenant_name,
                cluster_signature=cluster_sig,
                round_number=round_number + 1,
                previous_winner=previous_winner,
            )
            await persist_variant_pair(sb, tenant_id, cluster_sig, round_number + 1, va, vb)
            refreshed += 1
            log.info(
                "cron.weekly_cluster_refresh.refreshed",
                tenant_id=tenant_id,
                cluster=cluster_sig,
                old_round=round_number,
                new_round=round_number + 1,
                old_sent=total_sent,
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.warning(
                "cron.weekly_cluster_refresh.failed",
                tenant_id=tenant_id,
                cluster=cluster_sig,
                err=str(exc)[:200],
            )

    # Drift detection — unlock converged clusters that have been
    # locked for >= 90 days. Without this a winning copy stays
    # unchallenged forever even if the market shifts (new
    # competitors, seasonal patterns, regulation changes). 90 days
    # is the same cadence as the chi-square evaluation window x
    # ~6, giving the operator one quarter of "set and forget" before
    # the system probes for drift on its own.
    drift_unlocked = 0
    drift_failed = 0
    drift_cutoff = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    drift_res = (
        sb.table("cluster_state")
        .select("tenant_id, cluster_signature, champion_variant_id")
        .lt("converged_at", drift_cutoff)
        .is_("unlocked_at", None)
        .limit(MAX_REFRESH_PER_RUN)
        .execute()
    )
    for row in drift_res.data or []:
        try:
            # Pull the old champion's copy as the baseline for the
            # new round so the new sfidante starts from the proven
            # winner instead of generating from scratch.
            champ_id = row.get("champion_variant_id")
            previous_winner = None
            if champ_id:
                champ_resp = (
                    sb.table("cluster_copy_variants")
                    .select(
                        "round_number, copy_subject, copy_opening_line, "
                        "copy_proposition_line, cta_primary_label"
                    )
                    .eq("id", champ_id)
                    .maybe_single()
                    .execute()
                )
                champ = champ_resp.data if champ_resp else None
                if champ:
                    previous_winner = {
                        "copy_subject": champ.get("copy_subject") or "",
                        "copy_opening_line": champ.get("copy_opening_line") or "",
                        "copy_proposition_line": champ.get("copy_proposition_line") or "",
                        "cta_primary_label": champ.get("cta_primary_label") or "",
                    }
                    next_round = int(champ.get("round_number") or 1) + 1
                else:
                    next_round = 1
            else:
                next_round = 1

            # Reset cluster_state — the new round is a fresh test.
            (
                sb.table("cluster_state")
                .upsert(
                    {
                        "tenant_id": row["tenant_id"],
                        "cluster_signature": row["cluster_signature"],
                        "consecutive_wins": 0,
                        "last_winner_label": None,
                        "converged_at": None,
                        "champion_variant_id": None,
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                )
                .execute()
            )

            # Generate the new sfidante pair.
            tn_resp = (
                sb.table("tenants")
                .select("business_name")
                .eq("id", row["tenant_id"])
                .single()
                .execute()
            )
            tenant_name = (tn_resp.data or {}).get("business_name") or "SolarLead"

            from ..services.variant_generator_service import (
                generate_variant_pair,
                persist_variant_pair,
            )

            va, vb = await generate_variant_pair(
                tenant_name=tenant_name,
                cluster_signature=row["cluster_signature"],
                round_number=next_round,
                previous_winner=previous_winner,
            )
            await persist_variant_pair(
                sb,
                row["tenant_id"],
                row["cluster_signature"],
                next_round,
                va,
                vb,
            )
            drift_unlocked += 1
            log.info(
                "cluster_ab.drift_refresh_triggered",
                tenant_id=row["tenant_id"],
                cluster=row["cluster_signature"],
                new_round=next_round,
            )
        except Exception as exc:  # noqa: BLE001
            drift_failed += 1
            log.warning(
                "cluster_ab.drift_refresh_failed",
                tenant_id=row.get("tenant_id"),
                cluster=row.get("cluster_signature"),
                err=str(exc)[:200],
            )

    summary = {
        "ok": True,
        "candidates": len(grouped),
        "refreshed": refreshed,
        "skipped_high_volume": skipped_high_volume,
        "failed": failed,
        "capped": refreshed >= MAX_REFRESH_PER_RUN,
        "drift_unlocked": drift_unlocked,
        "drift_failed": drift_failed,
    }
    log.info("cron.weekly_cluster_refresh.done", **summary)
    return summary


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

# Pipeline states a lead must be in to be escalated to `to_call`: it was
# contacted + followed up but has not turned into a live conversation.
_ESCALATABLE_STATUSES = {
    LeadStatus.SENT.value,
    LeadStatus.DELIVERED.value,
    LeadStatus.OPENED.value,
    LeadStatus.CLICKED.value,
}


def _followup_history(sb: Any, lead_id: str) -> tuple[bool, int]:
    """Read a lead's outreach history → (has_step4, followup_count).

    ``followup_count`` counts every send beyond the first touch
    (sequence_step >= 2) — the hard cap in followup_scenario_service.
    """
    rows = (
        sb.table("outreach_sends").select("sequence_step").eq("lead_id", lead_id).execute()
    ).data or []
    steps = [int(r.get("sequence_step") or 0) for r in rows]
    return (4 in steps), sum(1 for s in steps if s >= 2)


async def _maybe_escalate_to_call(
    sb: Any, lead: dict[str, Any], now: datetime, followup_count: int
) -> bool:
    """Hand a stalled-but-interested lead to the operator for a phone call.

    Fires when a lead that received at least one follow-up has gone
    silent for 24h+ past that follow-up and is not getting another email
    (the caller only invokes this when the scenario decision said "no
    send"). Moves it to ``to_call`` and notifies the operator.
    """
    if followup_count < 1:
        return False
    if lead.get("outreach_replied_at"):
        return False
    if str(lead.get("pipeline_status") or "") not in _ESCALATABLE_STATUSES:
        return False
    last_fu = _parse_ts(lead.get("last_followup_sent_at"))
    if last_fu is None or (now - last_fu) < timedelta(hours=24):
        return False
    sb.table("leads").update(
        {
            "pipeline_status": LeadStatus.TO_CALL.value,
            "last_status_transition_at": now.isoformat(),
        }
    ).eq("id", lead["id"]).execute()
    await _notify_inapp(
        tenant_id=str(lead["tenant_id"]),
        title="Lead da chiamare: follow-up senza risposta",
        body=(
            "Ha ricevuto i follow-up e non risponde da oltre 24h. "
            "Ha mostrato interesse — passa a una chiamata."
        ),
        severity="warning",
        href=f"/leads/{lead['id']}",
        metadata={"lead_id": str(lead["id"]), "reason": "followup_no_reply"},
    )
    return True


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
    now = datetime.now(UTC)
    cold_cutoff = (now - timedelta(days=STEP_4_DELAY_DAYS + 1)).isoformat()
    manual_cooldown = (now - timedelta(hours=24)).isoformat()

    # Tenants that have disabled the auto follow-up cron — skip every
    # lead belonging to them. Single SELECT keeps the cron fast.
    disabled_tenants_res = (
        sb.table("tenants").select("id").eq("followup_auto_enabled", False).execute()
    )
    disabled_tenant_ids = {str(r["id"]) for r in (disabled_tenants_res.data or [])}

    # Pull leads worth evaluating: have an initial outreach, not in
    # terminal states, and either currently engaged (score > 0) OR
    # cold-but-aged-out (eligible for cold scenario).
    # `to_call` is excluded too: once a lead is handed to the operator
    # for a phone call we stop the automated follow-up cadence.
    terminal = [
        LeadStatus.CLOSED_WON.value,
        LeadStatus.CLOSED_LOST.value,
        LeadStatus.BLACKLISTED.value,
        LeadStatus.TO_CALL.value,
    ]
    leads_res = (
        sb.table("leads")
        .select(
            "id, tenant_id, pipeline_status, outreach_sent_at, "
            "outreach_replied_at, engagement_score, engagement_peak_score, "
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
    escalated = 0
    skipped: dict[str, int] = {}

    for lead in leads:
        # Skip tenants with auto follow-up disabled.
        if str(lead.get("tenant_id")) in disabled_tenant_ids:
            skipped["auto_disabled"] = skipped.get("auto_disabled", 0) + 1
            continue

        # Skip lead with manual follow-up in the last 24h (cooldown).
        last_manual = lead.get("last_followup_sent_at")
        if last_manual and last_manual > manual_cooldown:
            skipped["manual_cooldown_24h"] = skipped.get("manual_cooldown_24h", 0) + 1
            continue

        # Read outreach history once: cold-cadence completion + the
        # follow-up count that caps the engagement cadence at 2.
        outreach_sent_at = _parse_ts(lead.get("outreach_sent_at"))
        has_step4, followup_count = _followup_history(sb, lead["id"])
        cold_complete = False
        if outreach_sent_at is not None and outreach_sent_at.isoformat() <= cold_cutoff:
            cold_complete = has_step4 or (
                (now - outreach_sent_at).days >= STEP_4_DELAY_DAYS + 7
            )

        snap = FollowupSnapshot(
            lead_id=str(lead["id"]),
            tenant_id=str(lead["tenant_id"]),
            pipeline_status=str(lead.get("pipeline_status") or ""),
            engagement_score=int(lead.get("engagement_score") or 0),
            engagement_peak_score=int(
                lead.get("engagement_peak_score") or lead.get("engagement_score") or 0
            ),
            last_engagement_at=_parse_ts(lead.get("last_portal_event_at")),
            initial_outreach_at=outreach_sent_at,
            last_followup_scenario=lead.get("last_followup_scenario"),
            last_followup_sent_at=_parse_ts(lead.get("last_followup_sent_at")),
            hot_lead_alerted_at=_parse_ts(lead.get("hot_lead_alerted_at")),
            cold_sequence_complete=cold_complete,
            followup_count=followup_count,
        )

        decision = evaluate_followup_scenario(snap, now=now)
        if not decision.should_act:
            reason = decision.reason or "no_action"
            skipped[reason] = skipped.get(reason, 0) + 1
            # A followed-up lead going silent 24h+ → hand to the operator
            # for a phone call (no further automated email is coming).
            if await _maybe_escalate_to_call(sb, lead, now, followup_count):
                escalated += 1
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
            sb.table("leads").update({"hot_lead_alerted_at": now.isoformat()}).eq(
                "id", snap.lead_id
            ).execute()
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
        escalated=escalated,
        candidates=len(leads),
        skipped=skipped,
    )
    return {
        "ok": True,
        "queued": queued,
        "notified_hot": notified_hot,
        "escalated": escalated,
        "candidates": len(leads),
        "skipped": skipped,
    }


async def engagement_followup_for_tenant(tenant_id: str) -> dict[str, Any]:
    """Run engagement follow-up evaluation for a single tenant immediately.

    Same logic as ``engagement_followup_cron`` but scoped to one tenant
    and callable on-demand (used by POST /v1/followup/trigger).
    """
    sb = get_service_client()
    now = datetime.now(UTC)
    cold_cutoff = (now - timedelta(days=STEP_4_DELAY_DAYS + 1)).isoformat()
    terminal = [
        LeadStatus.CLOSED_WON.value,
        LeadStatus.CLOSED_LOST.value,
        LeadStatus.BLACKLISTED.value,
        LeadStatus.TO_CALL.value,
    ]

    leads_res = (
        sb.table("leads")
        .select(
            "id, tenant_id, pipeline_status, outreach_sent_at, "
            "outreach_replied_at, engagement_score, engagement_peak_score, "
            "last_portal_event_at, last_followup_scenario, "
            "last_followup_sent_at, hot_lead_alerted_at"
        )
        .eq("tenant_id", tenant_id)
        .not_.is_("outreach_sent_at", "null")
        .not_.in_("pipeline_status", terminal)
        .order("engagement_score", desc=True)
        .limit(ENGAGEMENT_FOLLOWUP_BATCH)
        .execute()
    )
    leads = leads_res.data or []

    queued = 0
    escalated = 0
    skipped: dict[str, int] = {}

    for lead in leads:
        outreach_sent_at = _parse_ts(lead.get("outreach_sent_at"))
        has_step4, followup_count = _followup_history(sb, lead["id"])
        cold_complete = False
        if outreach_sent_at is not None and outreach_sent_at.isoformat() <= cold_cutoff:
            cold_complete = has_step4 or (
                (now - outreach_sent_at).days >= STEP_4_DELAY_DAYS + 7
            )

        snap = FollowupSnapshot(
            lead_id=str(lead["id"]),
            tenant_id=str(lead["tenant_id"]),
            pipeline_status=str(lead.get("pipeline_status") or ""),
            engagement_score=int(lead.get("engagement_score") or 0),
            engagement_peak_score=int(
                lead.get("engagement_peak_score") or lead.get("engagement_score") or 0
            ),
            last_engagement_at=_parse_ts(lead.get("last_portal_event_at")),
            initial_outreach_at=outreach_sent_at,
            last_followup_scenario=lead.get("last_followup_scenario"),
            last_followup_sent_at=_parse_ts(lead.get("last_followup_sent_at")),
            hot_lead_alerted_at=_parse_ts(lead.get("hot_lead_alerted_at")),
            cold_sequence_complete=cold_complete,
            followup_count=followup_count,
        )

        decision = evaluate_followup_scenario(snap, now=now)
        if not decision.should_act:
            reason = decision.reason or "no_action"
            skipped[reason] = skipped.get(reason, 0) + 1
            if await _maybe_escalate_to_call(sb, lead, now, followup_count):
                escalated += 1
            continue

        if decision.notify_only:
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
            sb.table("leads").update({"hot_lead_alerted_at": now.isoformat()}).eq(
                "id", snap.lead_id
            ).execute()
            continue

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

    return {
        "ok": True,
        "queued": queued,
        "escalated": escalated,
        "candidates": len(leads),
        "skipped": skipped,
    }


def _parse_ts(raw: Any) -> datetime | None:
    """Parse Supabase ISO timestamp strings defensively."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    s = str(raw).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Imminence Predictor — daily ranking of "leads to call today".
# ---------------------------------------------------------------------------


async def imminence_predictions_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily 06:30 UTC — refresh per-tenant ``lead_imminence_predictions``.

    Runs after the 04:00 engagement_rollup_cron (so engagement_score is
    fresh) and before the 07:30 follow_up_cron + 08:30 sla_first_touch
    (so the operator opening the dashboard at 09:00 local sees a
    populated list). Per-tenant batch keeps Haiku spend bounded — only
    leads with deterministic score >= 60 trigger an LLM call.

    No notifications are sent in this MVP — the dashboard's `/leads`
    page surfaces today's predictions inline (badge "AI" + reasons
    expandable on each row).
    """
    from ..services.imminence_reasoning_service import generate_reasoning
    from ..services.imminence_service import run_imminence_predictions_for_tenant

    sb = get_service_client()
    # `tenants.status` is the activity flag in this schema; we run the
    # predictor only for tenants in 'active' (skip churned/suspended).
    tenants_res = sb.table("tenants").select("id, status").execute()
    tenant_ids = [
        t["id"] for t in (tenants_res.data or []) if (t.get("status") or "active") == "active"
    ]

    total_scored = 0
    total_reasoned = 0
    errors = 0
    for tid in tenant_ids:
        try:
            res = await run_imminence_predictions_for_tenant(tid, reasoning_fn=generate_reasoning)
            total_scored += res.get("scored", 0)
            total_reasoned += res.get("reasoned", 0)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            log.warning("cron.imminence.tenant_failed", tenant_id=tid, err=str(exc))

    log.info(
        "cron.imminence.complete",
        tenants=len(tenant_ids),
        scored=total_scored,
        reasoned=total_reasoned,
        errors=errors,
    )
    return {
        "tenants": len(tenant_ids),
        "scored": total_scored,
        "reasoned": total_reasoned,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Practice deadlines — daily monitor (Livello 2 Sprint 1).
# ---------------------------------------------------------------------------


async def practice_deadlines_cron(_ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily sweep of ``practice_deadlines`` open rows past their due date.

    For each newly-overdue row:
      1. flip status to ``'overdue'``,
      2. record an ``EVT_DEADLINE_BREACHED`` event,
      3. insert a tenant-wide notification (severity=warning) so the
         bell lights up the next time a member opens the dashboard.

    The function delegates to ``mark_overdue_and_notify``; this cron
    is just the schedule + structured logging.  Idempotent in practice
    because the partial index on (status='open') means a re-run on the
    same day finds zero matching rows.
    """
    from ..services.practice_deadlines_service import mark_overdue_and_notify

    summary = await asyncio.to_thread(mark_overdue_and_notify)
    log.info(
        "cron.practice_deadlines.complete",
        newly_overdue=summary.get("newly_overdue", 0),
        errors=summary.get("errors", 0),
    )
    return summary
