"""Usage & quota read-only endpoints.

These routes back the dashboard widgets that show the customer
"how much of today's budget have I burned." They are *read-only* —
no INCRs happen here; the actual reservation lives on the outreach
hot path (``OutreachAgent.execute``) so a dashboard refresh never
costs a send.

Routes:
  GET /v1/usage/daily-target   Sprint 2: tenant's "in-target" daily cap
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..services import daily_target_cap_service

log = get_logger(__name__)
router = APIRouter()


@router.get("/daily-target")
async def get_daily_target_usage(ctx: CurrentUser) -> dict[str, Any]:
    """Return the tenant's daily 'in-target' send budget snapshot.

    Response shape (designed for the ``DailyCapWidget`` in dashboard):
        {
          "used":      127,    # in-target sends consumed today (Europe/Rome)
          "limit":     250,    # tenant's daily cap (default 250)
          "remaining": 123,    # max(0, limit - used)
          "verdict":   "allowed" | "cap_reached"
        }

    The window resets at local midnight in Europe/Rome — matching the
    customer's mental model and the counter key in Redis.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    res = (
        sb.table("tenants")
        .select("id, daily_target_send_cap")
        .eq("id", tenant_id)
        .single()
        .execute()
    )
    tenant_row = res.data or {}
    if not tenant_row:
        raise HTTPException(status_code=404, detail="tenant not found")

    decision = await daily_target_cap_service.peek_usage(tenant_row)
    return {
        "used": decision.used,
        "limit": decision.limit,
        "remaining": decision.remaining,
        "verdict": decision.verdict,
    }
