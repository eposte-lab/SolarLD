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
from .level1_places import run_level1_places
from .level2_scraping import run_level2_scraping
from .level3_quality import run_level3_quality
from .level4_solar_qualify import run_level4_solar_qualify
from .level5_proxy_score import run_level5_proxy_score
from .types_v3 import FunnelV3Context, ScoredV3Candidate

log = get_logger(__name__)


async def run_funnel_v3(
    *,
    tenant_id: str,
    config: Any,  # TenantConfig — duck-typed; only target_wizard_groups used
    emitter: Any | None = None,  # optional event emitter (HunterAgent._emit_event)
    max_l1_candidates: int = 2000,
) -> dict[str, Any]:
    """Run L1→L5 for one tenant. Returns a summary dict for logging.

    Caller responsibilities:
      * Verify the tenant has rows in tenant_target_areas (otherwise
        L1 returns empty and we short-circuit).
      * Enqueue this task only for tenants that opted into v3.

    The function is **idempotent at the upsert level** — re-running on
    the same day re-scrapes/re-scores existing candidates rather than
    duplicating them (scan_candidates is keyed on
    (tenant_id, google_place_id)).
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

    # ---- L1 Places ----
    l1 = await run_level1_places(ctx)
    await costs.flush()
    await _emit(
        "scan.l1_complete",
        {"candidates": len(l1), "places_cost_cents": costs.places_cost_cents},
    )
    summary["stages"]["l1"] = {"candidates": len(l1)}
    if not l1:
        await _emit("scan.completed", {"total_cost_cents": costs.total_cost_cents()})
        await costs.flush(completed=True)
        return summary

    # ---- L2 Scraping ----
    l2 = await run_level2_scraping(ctx, l1)
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
        await _emit("scan.completed", {"total_cost_cents": costs.total_cost_cents()})
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
        await _emit("scan.completed", {"total_cost_cents": costs.total_cost_cents()})
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
        await _emit("scan.completed", {"total_cost_cents": costs.total_cost_cents()})
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

    # Mark recommended ones for L6 (existing FLUSSO 3 picks them up).
    for r in recommended:
        costs.mark_lead_qualified()

    await _emit(
        "scan.completed",
        {
            "total_cost_cents": costs.total_cost_cents(),
            "lead_count": len(recommended),
        },
    )
    await costs.flush(completed=True)

    summary["total_cost_cents"] = costs.total_cost_cents()
    summary["lead_count"] = len(recommended)
    return summary
