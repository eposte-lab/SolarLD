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
