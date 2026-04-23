"""Campaigns — backward-compat alias for outreach_sends.

The ``campaigns`` table was renamed to ``outreach_sends`` in migration 0043.
This route is kept at ``/v1/campaigns`` so existing dashboard pages that
still import from ``lib/data/campaigns.ts`` continue to work during the
transition. New code should call ``/v1/outreach-sends`` directly.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client

router = APIRouter()


@router.get("")
async def list_campaigns(ctx: CurrentUser) -> list[dict[str, object]]:
    """Backward-compat list of outreach sends (was: campaigns)."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("outreach_sends")
        .select("*")
        .eq("tenant_id", tenant_id)
        .order("scheduled_for", desc=True)
        .limit(200)
        .execute()
    )
    return res.data or []
