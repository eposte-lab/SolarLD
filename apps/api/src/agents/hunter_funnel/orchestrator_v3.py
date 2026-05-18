"""FLUSSO 1 v3 orchestrator — geocentric, no-Atoka.

Composes the v3 levels in sequence:

    L0 (already done in onboarding) → tenant_target_areas
    L1 places           → discovers candidates per zone
    L2 scraping         → enriches each candidate from public sources
    L3 quality filter   → drops low-signal candidates (heuristics)
    L4 solar qualify    → direct Solar API call on Places coords
    L5 proxy score      → Haiku ranks survivors 0-100
    L6 (existing FLUSSO 3) → asset generation only for recommended ones

The trigger is the daily 05:30 UTC cron in workers/main.py: for every
tenant with active rows in `tenant_target_areas`, enqueue a
``hunter_funnel_v3_task``. Tenants without zones are silently skipped
(they may still be on v2 Atoka path until the demolition).

Events emitted (powering the dashboard funnel waterfall):
  scan.l1_complete  — places discovery, count + cost
  scan.l2_complete  — scraping, count + email_rate
  scan.l3_complete  — quality, accepted/rejected
  scan.l4_complete  — solar, accepted_with_roof
  scan.l5_complete  — score, recommended_for_rendering
  scan.completed    — total cost + lead count
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from ...core.logging import get_logger
from ...services.scan_cost_tracker import ScanCostAccumulator
from .level1_places import load_backlog, mark_processed, run_level1_places
from .level2_scraping import run_level2_scraping
from .level3_quality import run_level3_quality
from .level4_solar_qualify import run_level4_solar_qualify
from .level5_proxy_score import run_level5_proxy_score
from .level6_promote_to_leads import run_level6_promote_to_leads
from .types_v3 import FunnelV3Context, ScoredV3Candidate

log = get_logger(__name__)


async def run_funnel_v3(
    *,
    tenant_id: str,
    config: Any,  # TenantConfig — duck-typed; only target_wizard_groups used
    emitter: Any | None = None,  # optional event emitter (HunterAgent._emit_event)
    max_l1_candidates: int = 2000,
    comune: str | None = None,
    province_code: str | None = None,
    scan_job_id: str | None = None,
) -> dict[str, Any]:
    """Run the funnel for one scan job. Returns a summary dict.

    Two phases:
      * L1 discovery — grow the candidate pool with genuinely NEW
        places for this comune (skips zones discovered recently).
      * L2-L6 processing — pull the next batch of un-processed
        candidates (the consumption cursor: `processed_at IS NULL`),
        run them through scraping → quality → solar → score → promote,
        then stamp them processed so the next run continues from the
        following batch.

    ``comune`` scopes both phases to one comune so a tenant's scan
    jobs on different territories stay isolated.
    """
    scan_id = str(uuid4())

    costs = ScanCostAccumulator(
        tenant_id=tenant_id,
        scan_id=scan_id,
        scan_mode="v3_funnel",
        territory_id=None,
    )

    ctx = FunnelV3Context(
        tenant_id=tenant_id,
        scan_id=scan_id,
        config=config,
        costs=costs,
        comune=comune,
        province_code=province_code,
        scan_job_id=scan_job_id,
        max_l1_candidates=max_l1_candidates,
    )

    summary: dict[str, Any] = {
        "tenant_id": tenant_id,
        "scan_id": scan_id,
        "stages": {},
    }

    async def _emit(event_type: str, payload: dict[str, Any]) -> None:
        if emitter is None:
            return
        try:
            await emitter(
                event_type=event_type,
                payload={"scan_id": scan_id, **payload},
                tenant_id=tenant_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("orchestrator_v3.emit_failed", err=type(exc).__name__)

    # ---- L1 Places discovery — grow the pool with NEW candidates ----
    l1_disc = await run_level1_places(ctx)
    await costs.flush()

    # ---- Consumption cursor — pull the next un-processed batch ----
    batch = await load_backlog(ctx, limit=ctx.max_l1_candidates)
    await _emit(
        "scan.l1_complete",
        {
            "candidates": len(batch),
            "discovered": l1_disc.get("discovered", 0),
            "places_cost_cents": costs.places_cost_cents,
        },
    )
    # `candidates` here = batch size processed this run. The worker
    # state machine reads it: 0 → no work left (territory consumed).
    summary["stages"]["l1"] = {
        "candidates": len(batch),
        "discovered": l1_disc.get("discovered", 0),
        "zones_skipped_fresh": l1_disc.get("zones_skipped_fresh", 0),
    }
    if not batch:
        await _emit("scan.completed", {"total_cost_cents": costs.total_cost_cents})
        await costs.flush(completed=True)
        return summary

    batch_ids = [str(b.candidate_id) for b in batch]

    # ---- L2 Scraping ----
    l2 = await run_level2_scraping(ctx, batch)
    await costs.flush()
    await _emit(
        "scan.l2_complete",
        {
            "candidates": len(l2),
            "with_email": sum(1 for s in l2 if s.contact.best_email),
        },
    )
    summary["stages"]["l2"] = {
        "candidates": len(l2),
        "with_email": sum(1 for s in l2 if s.contact.best_email),
    }
    if not l2:
        await mark_processed(ctx, batch_ids)
        await _emit("scan.completed", {"total_cost_cents": costs.total_cost_cents})
        await costs.flush(completed=True)
        return summary

    # ---- L3 Quality ----
    l3 = await run_level3_quality(ctx, l2)
    await costs.flush()
    await _emit(
        "scan.l3_complete",
        {"accepted": len(l3), "rejected": len(l2) - len(l3)},
    )
    summary["stages"]["l3"] = {"accepted": len(l3), "rejected": len(l2) - len(l3)}
    if not l3:
        await mark_processed(ctx, batch_ids)
        await _emit("scan.completed", {"total_cost_cents": costs.total_cost_cents})
        await costs.flush(completed=True)
        return summary

    # ---- L4 Solar Qualify ----
    l4 = await run_level4_solar_qualify(ctx, l3)
    await costs.flush()
    accepted = [c for c in l4 if c.solar_verdict == "accepted"]
    await _emit(
        "scan.l4_complete",
        {
            "scanned": len(l4),
            "accepted": len(accepted),
            "solar_cost_cents": costs.solar_cost_cents,
        },
    )
    summary["stages"]["l4"] = {"scanned": len(l4), "accepted": len(accepted)}
    if not accepted:
        await mark_processed(ctx, batch_ids)
        await _emit("scan.completed", {"total_cost_cents": costs.total_cost_cents})
        await costs.flush(completed=True)
        return summary

    # ---- L5 Proxy Score ----
    l5: list[ScoredV3Candidate] = await run_level5_proxy_score(ctx, l4)
    await costs.flush()
    recommended = [s for s in l5 if s.recommended_for_rendering]
    await _emit(
        "scan.l5_complete",
        {
            "scored": len(l5),
            "recommended": len(recommended),
            "claude_cost_cents": costs.claude_cost_cents,
        },
    )
    summary["stages"]["l5"] = {"scored": len(l5), "recommended": len(recommended)}

    # ---- L6 Promote to leads ----
    # Materialise the recommended scan_candidates into real subjects+leads
    # so that the existing FLUSSO 3 (creative + outreach agents) can pick
    # them up with the standard pipeline_status='ready_to_send' filter.
    leads_inserted = await run_level6_promote_to_leads(ctx, l5)
    for _ in range(leads_inserted):
        costs.mark_lead_qualified()
    await _emit(
        "scan.l6_complete",
        {
            "recommended": len(recommended),
            "leads_inserted": leads_inserted,
        },
    )
    summary["stages"]["l6"] = {
        "recommended": len(recommended),
        "leads_inserted": leads_inserted,
    }

    # Stamp the whole batch consumed — next run continues from the
    # following un-processed candidates (the territory cursor advances).
    await mark_processed(ctx, batch_ids)

    await _emit(
        "scan.completed",
        {
            "total_cost_cents": costs.total_cost_cents,
            "lead_count": leads_inserted,
        },
    )
    await costs.flush(completed=True)

    summary["total_cost_cents"] = costs.total_cost_cents
    summary["lead_count"] = leads_inserted
    return summary
