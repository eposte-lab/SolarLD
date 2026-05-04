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


class ScanStageSummary(BaseModel):
    l1_candidates: int
    l2_with_email: int
    l3_accepted: int
    l4_solar_accepted: int
    l5_recommended: int
    total_cost_eur: float
    started_at: str | None
    completed_at: str | None


class ScanCandidateOut(BaseModel):
    id: str
    google_place_id: str | None
    business_name: str | None
    predicted_sector: str | None
    stage: int
    building_quality_score: int | None
    solar_verdict: str | None
    overall_score: int | None
    recommended_for_rendering: bool
    lat: float | None
    lng: float | None
    website: str | None
    phone: str | None
    best_email: str | None
    created_at: str


class ScanResultsResponse(BaseModel):
    summary: ScanStageSummary
    top_candidates: list[ScanCandidateOut]
    scan_id: str | None


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
        job_id=f"map_target_areas:{tenant_id}",
    )
    return MapTerritoryResponse(
        job_id=job.get("job_id", f"already_running:{tenant_id}") if job else f"already_running:{tenant_id}",
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
        job_id=f"funnel_v3_manual:{tenant_id}",
    )
    return RunFunnelResponse(
        job_id=job.get("job_id", f"already_running:{tenant_id}") if job else f"already_running:{tenant_id}",
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


@router.get("/scan-results", response_model=ScanResultsResponse)
async def scan_results(ctx: CurrentUser) -> ScanResultsResponse:
    """Latest v3 funnel scan results for this tenant.

    Returns:
    * A stage-by-stage funnel summary (L1→L5 counts + cost).
    * Top recommended candidates (recommended_for_rendering=True),
      ordered by overall_score DESC, capped at 50.

    The summary is derived from the most recent ``scan_cost_log`` row
    for this tenant with ``scan_mode='v3_funnel'``, plus a live query
    of ``scan_candidates`` for per-stage counts.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # ---- Cost log (latest v3 scan) ----
    cost_res = (
        sb.table("scan_cost_log")
        .select(
            "scan_id, candidates_l1, candidates_l2, candidates_l3, candidates_l4, "
            "leads_qualified, total_cost_cents, started_at, completed_at"
        )
        .eq("tenant_id", tenant_id)
        .eq("scan_mode", "v3_funnel")
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )
    cost_row = (cost_res.data or [None])[0]
    latest_scan_id: str | None = cost_row["scan_id"] if cost_row else None

    # ---- Live stage counts from scan_candidates ----
    cands_res = (
        sb.table("scan_candidates")
        .select("stage, building_quality_score, solar_verdict, recommended_for_rendering, contact_extraction")
        .eq("tenant_id", tenant_id)
        .eq("funnel_version", 3)
        .execute()
    )
    rows = cands_res.data or []

    l1 = len(rows)
    l2_with_email = sum(
        1 for r in rows
        if (r.get("contact_extraction") or {}).get("best_email")
    )
    l3_accepted = sum(
        1 for r in rows
        if r.get("building_quality_score") is not None and r["building_quality_score"] >= 3
    )
    l4_solar = sum(
        1 for r in rows if r.get("solar_verdict") == "accepted"
    )
    l5_recommended = sum(1 for r in rows if r.get("recommended_for_rendering"))

    summary = ScanStageSummary(
        l1_candidates=l1,
        l2_with_email=l2_with_email,
        l3_accepted=l3_accepted,
        l4_solar_accepted=l4_solar,
        l5_recommended=l5_recommended,
        total_cost_eur=(cost_row["total_cost_cents"] or 0) / 100.0 if cost_row else 0.0,
        started_at=cost_row["started_at"] if cost_row else None,
        completed_at=cost_row["completed_at"] if cost_row else None,
    )

    # ---- Top recommended candidates ----
    top_res = (
        sb.table("scan_candidates")
        .select(
            "id, google_place_id, business_name, predicted_sector, stage, "
            "building_quality_score, solar_verdict, proxy_score_data, "
            "recommended_for_rendering, enrichment, contact_extraction, created_at"
        )
        .eq("tenant_id", tenant_id)
        .eq("funnel_version", 3)
        .eq("recommended_for_rendering", True)
        .order("stage", desc=True)
        .limit(50)
        .execute()
    )

    top_candidates: list[ScanCandidateOut] = []
    for r in (top_res.data or []):
        place_blob = (r.get("enrichment") or {}).get("places") or {}
        score_blob = r.get("proxy_score_data") or {}
        contact_blob = r.get("contact_extraction") or {}
        top_candidates.append(
            ScanCandidateOut(
                id=r["id"],
                google_place_id=r.get("google_place_id"),
                business_name=r.get("business_name") or place_blob.get("display_name"),
                predicted_sector=r.get("predicted_sector"),
                stage=r.get("stage", 1),
                building_quality_score=r.get("building_quality_score"),
                solar_verdict=r.get("solar_verdict"),
                overall_score=score_blob.get("overall_score"),
                recommended_for_rendering=bool(r.get("recommended_for_rendering")),
                lat=place_blob.get("lat"),
                lng=place_blob.get("lng"),
                website=place_blob.get("website"),
                phone=place_blob.get("phone"),
                best_email=contact_blob.get("best_email"),
                created_at=r["created_at"],
            )
        )

    # Sort by overall_score desc (nulls last)
    top_candidates.sort(key=lambda c: c.overall_score or 0, reverse=True)

    return ScanResultsResponse(
        summary=summary,
        top_candidates=top_candidates,
        scan_id=latest_scan_id,
    )
