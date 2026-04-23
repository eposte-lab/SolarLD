"""Territory management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..core.queue import enqueue
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..models.territory import TerritoryCreate, TerritoryOut
from ..services.hunter.grid import estimate_grid_cost

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


@router.get("/{territory_id}/scan-estimate")
async def scan_estimate(
    ctx: CurrentUser,
    territory_id: str,
    step_meters: float = Query(50.0, ge=10.0, le=500.0),
) -> dict[str, object]:
    """Pre-flight budget check: points + cost before actually enqueuing."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("territories")
        .select("bbox, name")
        .eq("id", territory_id)
        .eq("tenant_id", tenant_id)
        .single()
        .execute()
    )
    if not res.data or not res.data.get("bbox"):
        raise HTTPException(status_code=404, detail="territory not found or missing bbox")
    est = estimate_grid_cost(res.data["bbox"], step_meters=step_meters)
    return {
        "territory_id": territory_id,
        "name": res.data.get("name"),
        "step_meters": step_meters,
        **est,
    }


_VALID_SCAN_MODES = {"b2b_funnel_v2", "b2c_residential"}


@router.post("/{territory_id}/scan")
async def trigger_scan(
    ctx: CurrentUser,
    territory_id: str,
    max_roofs: int = Query(500, ge=1, le=10_000),
    start_index: int = Query(0, ge=0),
    step_meters: float = Query(50.0, ge=10.0, le=500.0),
    scan_mode_override: str | None = Query(
        None,
        description=(
            "Force a specific scan mode, bypassing the tenant's sorgente config. "
            "Valid values: 'b2b_funnel_v2', 'b2c_residential'. "
            "Useful for testing with ATOKA_MOCK_MODE=true without touching the wizard."
        ),
    ),
) -> dict[str, object]:
    """Enqueue a Hunter Agent scan for this territory.

    Returns the job id so the dashboard can poll scan progress via
    `/v1/events?source=agent.hunter&territory_id=...`.
    """
    tenant_id = require_tenant(ctx)

    if scan_mode_override is not None and scan_mode_override not in _VALID_SCAN_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"scan_mode_override must be one of {sorted(_VALID_SCAN_MODES)}",
        )

    # Quick sanity check the territory exists and belongs to the tenant
    sb = get_service_client()
    t = (
        sb.table("territories")
        .select("id")
        .eq("id", territory_id)
        .eq("tenant_id", tenant_id)
        .single()
        .execute()
    )
    if not t.data:
        raise HTTPException(status_code=404, detail="territory not found")

    payload: dict[str, object] = {
        "tenant_id": tenant_id,
        "territory_id": territory_id,
        "max_roofs": max_roofs,
        "start_index": start_index,
        "step_meters": step_meters,
    }
    if scan_mode_override:
        payload["scan_mode_override"] = scan_mode_override

    job = await enqueue(
        "hunter_task",
        payload,
        # Idempotency: one in-flight scan per (tenant, territory, start_index)
        job_id=f"hunter:{tenant_id}:{territory_id}:{start_index}",
    )
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "territory_id": territory_id,
        "max_roofs": max_roofs,
        **job,
    }
