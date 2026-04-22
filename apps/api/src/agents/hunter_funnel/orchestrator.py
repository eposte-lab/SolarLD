"""Funnel v2 orchestrator — wires L1 → L2 → L3 → L4 with cost tracking
and event emission.

Called from `HunterAgent._run_b2b_funnel_v2`. Kept separate from the
agent class so the funnel can be invoked directly (e.g. from a test, or a
future admin re-scan endpoint) without going through the Hunter wrapper.

Events emitted (drive the dashboard's funnel waterfall):
  - `scan.l1_complete` — Atoka discovery done, with count + cost
  - `scan.l2_complete` — enrichment done
  - `scan.l3_complete` — proxy scores computed
  - `scan.l4_complete` — Solar gate done, with qualified count
  - `scan.completed`    — terminal, carries total cost + lead count

On any level returning zero candidates we short-circuit the remaining
levels and still emit a `scan.completed` with the partial cost.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from ...core.logging import get_logger
from ...services.scan_cost_tracker import ScanCostAccumulator
from ..hunter import HunterAgent, HunterInput, HunterOutput
from .level1_discovery import run_level1
from .level2_enrichment import run_level2
from .level3_proxy_score import run_level3
from .level4_solar_gate import run_level4
from .types import FunnelContext

log = get_logger(__name__)


async def run_funnel(
    *,
    agent: HunterAgent,
    payload: HunterInput,
    config: dict[str, Any],
    territory: dict[str, Any],
) -> HunterOutput:
    """Run the four levels in sequence. Returns the populated HunterOutput
    that HunterAgent.execute will log into api_usage_log.
    """
    out = HunterOutput()

    # A scan_id groups all candidates from one invocation. Reused on retry
    # when payload carries an explicit scan_id override (not plumbed yet —
    # defer to when the `/v1/scans/{id}/resume` endpoint ships).
    scan_id = str(uuid4())

    costs = ScanCostAccumulator(
        tenant_id=payload.tenant_id,
        scan_id=scan_id,
        scan_mode="b2b_funnel_v2",
        territory_id=payload.territory_id,
    )

    ctx = FunnelContext(
        tenant_id=payload.tenant_id,
        scan_id=scan_id,
        territory_id=payload.territory_id,
        territory=territory,
        config=config,
        costs=costs,
        max_l1_candidates=payload.max_roofs,
    )

    # ---- L1: Atoka discovery ----
    l1 = await run_level1(ctx)
    await costs.flush()
    await agent._emit_event(
        event_type="scan.l1_complete",
        payload={
            "scan_id": scan_id,
            "territory_id": payload.territory_id,   # needed by dashboard scan-summary
            "candidates": len(l1),
            "atoka_cost_cents": costs.atoka_cost_cents,
        },
        tenant_id=payload.tenant_id,
    )
    out.places_found = len(l1)
    if not l1:
        await _finalise(ctx, out, agent)
        return out

    # ---- L2: Enrichment ----
    l2 = await run_level2(ctx, l1)
    await costs.flush()
    await agent._emit_event(
        event_type="scan.l2_complete",
        payload={
            "scan_id": scan_id,
            "candidates": len(l2),
            "with_website": sum(1 for e in l2 if e.enrichment.website),
        },
        tenant_id=payload.tenant_id,
    )
    out.places_deduped = len(l2)  # reusing field for L2 count
    if not l2:
        await _finalise(ctx, out, agent)
        return out

    # Budget gate — abort before the more expensive L3 Haiku calls.
    if costs.over_budget(ctx.config.budget_scan_eur):
        log.info(
            "funnel.budget_exceeded_before_l3",
            scan_id=scan_id,
            tenant_id=payload.tenant_id,
            total_cost_cents=costs.total_cost_cents,
            budget_eur=ctx.config.budget_scan_eur,
        )
        await _finalise(ctx, out, agent)
        return out

    # ---- L3: Haiku proxy score ----
    l3 = await run_level3(ctx, l2)
    await costs.flush()
    await agent._emit_event(
        event_type="scan.l3_complete",
        payload={
            "scan_id": scan_id,
            "scored": len(l3),
            "claude_cost_cents": costs.claude_cost_cents,
            "score_avg": (
                sum(s.score for s in l3) / len(l3) if l3 else 0
            ),
        },
        tenant_id=payload.tenant_id,
    )
    if not l3:
        await _finalise(ctx, out, agent)
        return out

    # Budget gate — abort before the most expensive L4 Solar API calls.
    if costs.over_budget(ctx.config.budget_scan_eur):
        log.info(
            "funnel.budget_exceeded_before_l4",
            scan_id=scan_id,
            tenant_id=payload.tenant_id,
            total_cost_cents=costs.total_cost_cents,
            budget_eur=ctx.config.budget_scan_eur,
        )
        await _finalise(ctx, out, agent)
        return out

    # ---- L4: Solar gate ----
    qualified = await run_level4(ctx, l3)
    await costs.flush()
    await agent._emit_event(
        event_type="scan.l4_complete",
        payload={
            "scan_id": scan_id,
            "gated": min(
                len(l3),
                max(ctx.solar_gate_min_candidates, int(len(l3) * ctx.solar_gate_pct)),
            ),
            "qualified": qualified,
            "solar_cost_cents": costs.solar_cost_cents,
        },
        tenant_id=payload.tenant_id,
    )
    out.roofs_discovered = qualified
    # For funnel v2, "filtered_out" = L3 survivors that didn't pass the
    # Solar technical filter. Skipped-below-gate are not "filtered", they
    # just never ran Solar — bucketed separately in scan_candidates.
    out.roofs_filtered_out = len(l3) - qualified

    await _finalise(ctx, out, agent)
    return out


async def _finalise(
    ctx: FunnelContext, out: HunterOutput, agent: HunterAgent
) -> None:
    """Write total cost into the HunterOutput + emit terminal event."""
    await ctx.costs.flush(completed=True)
    out.api_cost_cents = ctx.costs.total_cost_cents
    out.api_calls = (
        ctx.costs.candidates_l4  # Solar call count
        + ctx.costs.candidates_l2  # Places (future, 0 today)
    )
    await agent._emit_event(
        event_type="scan.completed",
        payload={
            "scan_id": ctx.scan_id,
            "territory_id": ctx.territory_id,        # needed by dashboard scan-summary
            "total_cost_cents": ctx.costs.total_cost_cents,
            "leads_qualified": ctx.costs.leads_qualified,
            "breakdown": {
                "atoka": ctx.costs.atoka_cost_cents,
                "places": ctx.costs.places_cost_cents,
                "claude": ctx.costs.claude_cost_cents,
                "solar": ctx.costs.solar_cost_cents,
                "mapbox": ctx.costs.mapbox_cost_cents,
            },
        },
        tenant_id=ctx.tenant_id,
    )
