"""Campaigns — send history."""

from __future__ import annotations

from fastapi import APIRouter

from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client

router = APIRouter()


@router.get("")
async def list_campaigns(ctx: CurrentUser) -> list[dict[str, object]]:
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("campaigns")
        .select("*")
        .eq("tenant_id", tenant_id)
        .order("scheduled_for", desc=True)
        .limit(200)
        .execute()
    )
    return res.data or []
