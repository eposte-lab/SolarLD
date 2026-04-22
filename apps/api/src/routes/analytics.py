"""Analytics endpoints — aggregate rollups across the tenant's pipeline.

All heavy lifting lives in Postgres functions (``analytics_*`` —
see migration 0016). The route layer is a thin wrapper that:

  1. Enforces tenant scoping via ``require_tenant(ctx)``.
  2. Calls the right RPC and returns the JSON as-is.

The functions are ``SECURITY DEFINER`` and scoped to a single
``p_tenant_id`` arg, so RLS is upheld at the function level.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client

router = APIRouter()
log = get_logger(__name__)


def _rpc(name: str, params: dict[str, Any]) -> Any:
    """Call a Postgres function and return the JSONB payload.

    The Supabase Python client's ``.rpc()`` returns a response whose
    ``.data`` field is already the deserialised JSON.
    """
    sb = get_service_client()
    try:
        res = sb.rpc(name, params).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("analytics.rpc_failed", fn=name, err=str(exc))
        raise HTTPException(
            status_code=502,
            detail=f"analytics rpc {name} failed",
        ) from exc
    return res.data


# ---------------------------------------------------------------------------
# Overview — lightweight landing tile for the dashboard header
# ---------------------------------------------------------------------------


@router.get("/overview")
async def analytics_overview(ctx: CurrentUser) -> dict[str, Any]:
    """MTD usage + last-30d funnel + last-30d spend in one call."""
    tenant_id = require_tenant(ctx)
    return {
        "usage_mtd": _rpc("analytics_usage_mtd", {"p_tenant_id": tenant_id}),
        "funnel_30d": _rpc("analytics_funnel", {"p_tenant_id": tenant_id}),
        "spend_by_provider_mtd": _rpc(
            "analytics_spend_by_provider",
            {"p_tenant_id": tenant_id},
        ),
    }


# ---------------------------------------------------------------------------
# Funnel — leads → sent → delivered → opened → clicked → engaged → signed
# ---------------------------------------------------------------------------


@router.get("/funnel")
async def analytics_funnel(
    ctx: CurrentUser,
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    tenant_id = require_tenant(ctx)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    from datetime import timedelta

    p_from = now - timedelta(days=days)
    return {
        "window_days": days,
        "from": p_from.isoformat(),
        "to": now.isoformat(),
        "counts": _rpc(
            "analytics_funnel",
            {
                "p_tenant_id": tenant_id,
                "p_from": p_from.isoformat(),
                "p_to": now.isoformat(),
            },
        ),
    }


# ---------------------------------------------------------------------------
# Spend — by provider + daily sparkline
# ---------------------------------------------------------------------------


@router.get("/spend")
async def analytics_spend(
    ctx: CurrentUser,
    days: int = Query(
        default=30,
        ge=1,
        le=365,
        description="Sparkline window. 30 = last 30 days daily spend.",
    ),
) -> dict[str, Any]:
    tenant_id = require_tenant(ctx)
    return {
        "by_provider_mtd": _rpc(
            "analytics_spend_by_provider",
            {"p_tenant_id": tenant_id},
        ),
        "daily": _rpc(
            "analytics_spend_daily",
            {"p_tenant_id": tenant_id, "p_days": days},
        ),
    }


# ---------------------------------------------------------------------------
# Territory ROI — contracts signed + lead counts per territory
# ---------------------------------------------------------------------------


@router.get("/territories")
async def analytics_territories(ctx: CurrentUser) -> list[dict[str, Any]]:
    tenant_id = require_tenant(ctx)
    return _rpc("analytics_territory_roi", {"p_tenant_id": tenant_id}) or []


# ---------------------------------------------------------------------------
# Scan funnel — L1 → L2 → L3 → L4 → leads → outreach waterfall
#
# Reads directly from scan_candidates + leads tables (no RPC needed since
# these are simple counts).  Gives the /funnel page its top-of-funnel
# numbers, separate from the post-outreach analytics_funnel RPC.
# ---------------------------------------------------------------------------


@router.get("/scan-funnel")
async def analytics_scan_funnel(ctx: CurrentUser) -> dict[str, Any]:
    """Full waterfall: scan_candidates stages + leads pipeline stages.

    Returns two blocks:
      * ``discovery`` — L1/L2/L3/L4 counts from scan_candidates
      * ``pipeline``  — leads/sent/delivered/opened/clicked/engaged/converted
      * ``cost``      — total spend cents and derived unit economics
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    def _sc_count(*, gte_stage: int | None = None, eq_stage: int | None = None,
                  solar_verdict: str | None = None) -> int:
        q = (
            sb.table("scan_candidates")
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
        )
        if gte_stage is not None:
            q = q.gte("stage", gte_stage)
        if eq_stage is not None:
            q = q.eq("stage", eq_stage)
        if solar_verdict is not None:
            q = q.eq("solar_verdict", solar_verdict)
        try:
            return q.execute().count or 0
        except Exception:  # noqa: BLE001
            return 0

    def _lead_count(**filters: str) -> int:
        q = sb.table("leads").select("id", count="exact").eq("tenant_id", tenant_id)
        for col, val in filters.items():
            if val is not None:
                q = q.eq(col, val)
        try:
            return q.execute().count or 0
        except Exception:  # noqa: BLE001
            return 0

    def _lead_count_not_null(col: str) -> int:
        try:
            return (
                sb.table("leads")
                .select("id", count="exact")
                .eq("tenant_id", tenant_id)
                .not_.is_(col, "null")
                .execute()
                .count or 0
            )
        except Exception:  # noqa: BLE001
            return 0

    def _conversion_count(stage: str) -> int:
        try:
            return (
                sb.table("conversions")
                .select("id", count="exact")
                .eq("tenant_id", tenant_id)
                .eq("stage", stage)
                .execute()
                .count or 0
            )
        except Exception:  # noqa: BLE001
            return 0

    # Discovery stages (top-of-funnel)
    l1 = _sc_count(gte_stage=1)
    l2 = _sc_count(gte_stage=2)
    l3 = _sc_count(gte_stage=3)
    l4_qualified = _sc_count(eq_stage=4, solar_verdict="accepted")
    l4_rejected = _sc_count(eq_stage=4, solar_verdict="rejected_tech")
    l4_skipped = _sc_count(eq_stage=4, solar_verdict="skipped_below_gate")

    # Pipeline stages (post-discovery)
    leads_total = _lead_count()
    leads_sent = _lead_count_not_null("outreach_sent_at")
    leads_delivered = _lead_count_not_null("outreach_delivered_at")
    leads_opened = _lead_count_not_null("outreach_opened_at")
    leads_clicked = _lead_count_not_null("outreach_clicked_at")
    leads_engaged = _lead_count(pipeline_status="engaged")
    leads_appointment = _lead_count(pipeline_status="appointment")
    leads_won = _lead_count(pipeline_status="closed_won")
    conversions_won = _conversion_count("won")

    # Aggregate scan cost from the events table (last 200 scan.completed events)
    total_scan_cost_cents = 0
    try:
        ev_res = (
            sb.table("events")
            .select("payload")
            .eq("tenant_id", tenant_id)
            .eq("event_type", "scan.completed")
            .order("occurred_at", desc=True)
            .limit(200)
            .execute()
        )
        for ev in (ev_res.data or []):
            p = ev.get("payload") or {}
            total_scan_cost_cents += int(p.get("total_cost_cents") or 0)
    except Exception:  # noqa: BLE001
        pass

    cost_per_contact = (
        round(total_scan_cost_cents / l1) if l1 > 0 else None
    )
    cost_per_lead = (
        round(total_scan_cost_cents / leads_total) if leads_total > 0 else None
    )
    cost_per_sent = (
        round(total_scan_cost_cents / leads_sent) if leads_sent > 0 else None
    )

    return {
        "discovery": {
            "l1": l1,
            "l2": l2,
            "l3": l3,
            "l4_qualified": l4_qualified,
            "l4_rejected": l4_rejected,
            "l4_skipped": l4_skipped,
        },
        "pipeline": {
            "leads_total": leads_total,
            "sent": leads_sent,
            "delivered": leads_delivered,
            "opened": leads_opened,
            "clicked": leads_clicked,
            "engaged": leads_engaged,
            "appointment": leads_appointment,
            "won": leads_won,
            "conversions_won": conversions_won,
        },
        "cost": {
            "total_scan_cost_cents": total_scan_cost_cents,
            "cost_per_contact_cents": cost_per_contact,
            "cost_per_lead_cents": cost_per_lead,
            "cost_per_sent_cents": cost_per_sent,
        },
    }
