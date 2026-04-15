"""Territory management endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..models.territory import TerritoryCreate, TerritoryOut

router = APIRouter()


@router.get("", response_model=list[TerritoryOut])
async def list_territories(ctx: CurrentUser) -> list[dict[str, object]]:
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("territories")
        .select("*")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data or []


@router.post("", response_model=TerritoryOut, status_code=201)
async def add_territory(ctx: CurrentUser, payload: TerritoryCreate) -> dict[str, object]:
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    row = {
        "tenant_id": tenant_id,
        "type": payload.type.value,
        "code": payload.code,
        "name": payload.name,
        "bbox": payload.bbox,
        "priority": payload.priority,
        "excluded": payload.excluded,
    }
    res = sb.table("territories").insert(row).execute()
    return res.data[0] if res.data else {}


@router.delete("/{territory_id}")
async def delete_territory(ctx: CurrentUser, territory_id: str) -> dict[str, bool]:
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    sb.table("territories").delete().eq("id", territory_id).eq("tenant_id", tenant_id).execute()
    return {"ok": True}


@router.post("/{territory_id}/scan")
async def trigger_scan(ctx: CurrentUser, territory_id: str) -> dict[str, object]:
    """Enqueue a Hunter Agent scan for this territory."""
    tenant_id = require_tenant(ctx)
    # TODO: enqueue BullMQ/arq job → hunter agent
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "territory_id": territory_id,
        "job_id": "pending",
        "message": "Scan job queued (stub — hunter agent not yet wired)",
    }
