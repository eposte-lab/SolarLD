"""Events (audit trail) — read-only tenant view."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client

router = APIRouter()


@router.get("")
async def list_events(
    ctx: CurrentUser,
    lead_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, object]]:
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    query = sb.table("events").select("*").eq("tenant_id", tenant_id)
    if lead_id:
        query = query.eq("lead_id", lead_id)
    if event_type:
        query = query.eq("event_type", event_type)
    res = query.order("occurred_at", desc=True).limit(limit).execute()
    return res.data or []
