"""Territory mapping endpoints — FLUSSO 1 v3 (geocentric, no-Atoka).

These routes drive the L0 stage of the new funnel:

  POST  /v1/territory/map     — kicks off the OSM zone mapping job
  GET   /v1/territory/status  — polls progress (job state + zone count)
  GET   /v1/territory/zones   — lists mapped polygons for visualisation

Behind the scenes the heavy lifting is done by the ARQ worker task
``map_target_areas_task`` (see workers/main.py). The endpoints here
only authenticate, validate input, and enqueue.

Tenant scoping: all reads are scoped via ``require_tenant`` and
service role; writes happen inside the worker (also service role).
RLS on ``tenant_target_areas`` keeps tenants isolated.

This is additive — co-exists with the legacy /v1/territories (Atoka-
based scan endpoints). When v3 reaches production, /v1/territories
will be deprecated.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..core.queue import enqueue
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client

router = APIRouter()


class MapTerritoryRequest(BaseModel):
    """Override input for the mapping run.

    By default the worker reads `target_wizard_groups` and the active
    province codes from the tenant's Sorgente module config. Operators
    can pass explicit values to override (e.g. for testing or to map a
    subset of the territory).
    """

    wizard_groups: list[str] | None = Field(
        default=None,
        description="If null, read from tenant_modules.config.sorgente.target_wizard_groups.",
    )
    province_codes: list[str] | None = Field(
        default=None,
        description="ISO 3166-2 suffixes (BS, BG, ...). If null, read from sorgente.province.",
    )


class MapTerritoryResponse(BaseModel):
    job_id: str
    tenant_id: str
    wizard_groups: list[str]
    province_codes: list[str]


class TerritoryStatusResponse(BaseModel):
    tenant_id: str
    zone_count: int
    sectors_covered: list[str]
    last_mapped_at: str | None


class TargetZoneOut(BaseModel):
    id: str
    osm_id: int
    osm_type: str
    centroid_lat: float
    centroid_lng: float
    area_m2: float | None
    matched_sectors: list[str]
    primary_sector: str | None
    matching_score: float | None
    province_code: str | None
    status: str


class RunFunnelRequest(BaseModel):
    """Optional overrides for a manual funnel run (testing / pilot)."""

    max_l1_candidates: int = Field(
        default=500,
        ge=10,
        le=2000,
        description="Cap Places candidates to keep costs low during testing.",
    )


class RunFunnelResponse(BaseModel):
    job_id: str
    tenant_id: str
    zone_count: int
    max_l1_candidates: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_sorgente_defaults(
    sb: Any, tenant_id: str
) -> tuple[list[str], list[str]]:
    """Read the tenant's Sorgente module to fill missing wizard_groups / provinces.

    The Sorgente JSONB has ``target_wizard_groups[]`` (Sprint A) and
    ``province[]`` (legacy field, list of "BS"-style codes). The L0
    mapping uses both.
    """
    res = (
        sb.table("tenant_modules")
        .select("config")
        .eq("tenant_id", tenant_id)
        .eq("module_key", "sorgente")
        .maybeSingle()
        .execute()
    )
    cfg = (res.data or {}).get("config") or {}
    wgs = list(cfg.get("target_wizard_groups") or [])
    provs = list(cfg.get("province") or [])
    return wgs, provs


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/map", response_model=MapTerritoryResponse, status_code=202)
async def map_territory(
    ctx: CurrentUser, body: MapTerritoryRequest = MapTerritoryRequest()
) -> MapTerritoryResponse:
    """Enqueue the L0 zone mapping job. Returns immediately with job_id.

    The actual mapping takes 2-15 minutes — clients should poll
    ``/v1/territory/status`` to know when it's done.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    wgs = body.wizard_groups
    provs = body.province_codes
    if not wgs or not provs:
        defaults_wgs, defaults_provs = _resolve_sorgente_defaults(sb, tenant_id)
        wgs = wgs or defaults_wgs
        provs = provs or defaults_provs

    if not wgs:
        raise HTTPException(
            status_code=400,
            detail="No wizard_groups available — configure them in the Sorgente module first.",
        )
    if not provs:
        raise HTTPException(
            status_code=400,
            detail="No province codes — set sorgente.province[] before mapping.",
        )

    job = await enqueue(
        "map_target_areas_task",
        {
            "tenant_id": tenant_id,
            "wizard_groups": wgs,
            "province_codes": provs,
        },
        _job_id=f"map_target_areas:{tenant_id}",
    )
    return MapTerritoryResponse(
        job_id=job.job_id if job else f"already_running:{tenant_id}",
        tenant_id=tenant_id,
        wizard_groups=wgs,
        province_codes=provs,
    )


@router.get("/status", response_model=TerritoryStatusResponse)
async def territory_status(ctx: CurrentUser) -> TerritoryStatusResponse:
    """Snapshot of how many zones are mapped + which sectors they cover."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    res = (
        sb.table("tenant_target_areas")
        .select("primary_sector, created_at")
        .eq("tenant_id", tenant_id)
        .eq("status", "active")
        .execute()
    )
    rows = res.data or []
    sectors = sorted({r.get("primary_sector") for r in rows if r.get("primary_sector")})
    last = max((r.get("created_at") for r in rows), default=None) if rows else None
    return TerritoryStatusResponse(
        tenant_id=tenant_id,
        zone_count=len(rows),
        sectors_covered=sectors,
        last_mapped_at=last,
    )


@router.post("/run-funnel", response_model=RunFunnelResponse, status_code=202)
async def run_funnel_manual(
    ctx: CurrentUser, body: RunFunnelRequest = RunFunnelRequest()
) -> RunFunnelResponse:
    """Manually trigger the L1→L5 funnel for this tenant (testing / pilot).

    Enqueues ``hunter_funnel_v3_task`` immediately — no need to wait
    for the 04:30 UTC cron. Safe to call multiple times; ARQ deduplicates
    by job_id (one running job per tenant at a time).

    Prerequisites:
      * L0 must have run first — ``tenant_target_areas`` must have ≥ 1 zone.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # Safety: abort if L0 hasn't run yet
    res = (
        sb.table("tenant_target_areas")
        .select("id", count="exact")
        .eq("tenant_id", tenant_id)
        .eq("status", "active")
        .execute()
    )
    zone_count = res.count or 0
    if zone_count == 0:
        raise HTTPException(
            status_code=409,
            detail=(
                "No active zones found for this tenant. "
                "Run POST /v1/territory/map first and wait for it to complete."
            ),
        )

    job = await enqueue(
        "hunter_funnel_v3_task",
        {
            "tenant_id": tenant_id,
            "max_l1_candidates": body.max_l1_candidates,
        },
        _job_id=f"funnel_v3_manual:{tenant_id}",
    )
    return RunFunnelResponse(
        job_id=job.job_id if job else f"already_running:{tenant_id}",
        tenant_id=tenant_id,
        zone_count=zone_count,
        max_l1_candidates=body.max_l1_candidates,
    )


@router.get("/zones", response_model=list[TargetZoneOut])
async def list_zones(
    ctx: CurrentUser,
    sector: str | None = Query(default=None, description="Filter by primary_sector."),
    province: str | None = Query(default=None, description="Filter by province code."),
    limit: int = Query(default=500, ge=1, le=5000),
) -> list[TargetZoneOut]:
    """List zones for visualisation. Returns centroid only (no full polygon).

    Polygon geometry is fetched on demand via /v1/territory/zones/{id}/geojson
    (TODO Sprint 4.6) to keep the list endpoint fast.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    q = (
        sb.table("tenant_target_areas")
        .select(
            "id, osm_id, osm_type, centroid_lat, centroid_lng, area_m2, "
            "matched_sectors, primary_sector, matching_score, province_code, status"
        )
        .eq("tenant_id", tenant_id)
        .eq("status", "active")
    )
    if sector:
        q = q.eq("primary_sector", sector)
    if province:
        q = q.eq("province_code", province.upper())
    res = q.order("matching_score", desc=True).limit(limit).execute()
    return [TargetZoneOut(**r) for r in (res.data or [])]
