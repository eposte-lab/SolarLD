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

import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query
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
    # L6 promotion counts (v3 lead pipeline progress) — added Sprint 8 so the
    # /territorio test panel can show step-by-step status without crawling
    # /leads itself.
    l6_leads_created: int = 0
    leads_with_rendering: int = 0
    leads_outreach_sent: int = 0
    total_cost_eur: float
    started_at: str | None
    completed_at: str | None
    is_running: bool = False


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


def _resolve_sorgente_defaults(sb: Any, tenant_id: str) -> tuple[list[str], list[str]]:
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
        .maybe_single()
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
        job_id=job.get("job_id", f"already_running:{tenant_id}")
        if job
        else f"already_running:{tenant_id}",
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

    # NB: job_id MUST be unique per click — ARQ silently rejects duplicates,
    # so a stale "funnel_v3_manual:<tenant>" entry in Redis would make every
    # subsequent button press a no-op until the dedup TTL expires. Adding the
    # epoch second guarantees a fresh id per manual trigger.
    job = await enqueue(
        "hunter_funnel_v3_task",
        {
            "tenant_id": tenant_id,
            "max_l1_candidates": body.max_l1_candidates,
        },
        job_id=f"funnel_v3_manual:{tenant_id}:{int(time.time())}",
    )
    return RunFunnelResponse(
        job_id=job.get("job_id", f"already_running:{tenant_id}")
        if job
        else f"already_running:{tenant_id}",
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
        .select(
            "stage, building_quality_score, solar_verdict, recommended_for_rendering, contact_extraction"
        )
        .eq("tenant_id", tenant_id)
        .eq("funnel_version", 3)
        .execute()
    )
    rows = cands_res.data or []

    l1 = len(rows)
    l2_with_email = sum(1 for r in rows if (r.get("contact_extraction") or {}).get("best_email"))
    l3_accepted = sum(
        1
        for r in rows
        if r.get("building_quality_score") is not None and r["building_quality_score"] >= 3
    )
    l4_solar = sum(1 for r in rows if r.get("solar_verdict") == "accepted")
    l5_recommended = sum(1 for r in rows if r.get("recommended_for_rendering"))

    # ---- L6 + downstream (creative + outreach) counts ----
    # We count leads created from v3 by joining via subjects.raw_data.source
    # which L6 stamps to 'funnel_v3'. RLS handles tenant scoping.
    l6_count = 0
    rendering_count = 0
    outreach_count = 0
    try:
        leads_res = (
            sb.table("leads")
            .select(
                "id, rendering_image_url, rendering_video_url, "
                "outreach_sent_at, subjects:subjects(raw_data)"
            )
            .eq("tenant_id", tenant_id)
            .execute()
        )
        for lr in leads_res.data or []:
            sub = lr.get("subjects") or {}
            raw = (sub.get("raw_data") or {}) if isinstance(sub, dict) else {}
            if raw.get("source") != "funnel_v3":
                continue
            l6_count += 1
            if lr.get("rendering_image_url") or lr.get("rendering_video_url"):
                rendering_count += 1
            if lr.get("outreach_sent_at"):
                outreach_count += 1
    except Exception:  # noqa: BLE001 — best-effort, panel still renders
        pass

    is_running = bool(cost_row and cost_row.get("started_at") and not cost_row.get("completed_at"))

    summary = ScanStageSummary(
        l1_candidates=l1,
        l2_with_email=l2_with_email,
        l3_accepted=l3_accepted,
        l4_solar_accepted=l4_solar,
        l5_recommended=l5_recommended,
        l6_leads_created=l6_count,
        leads_with_rendering=rendering_count,
        leads_outreach_sent=outreach_count,
        total_cost_eur=(cost_row["total_cost_cents"] or 0) / 100.0 if cost_row else 0.0,
        started_at=cost_row["started_at"] if cost_row else None,
        completed_at=cost_row["completed_at"] if cost_row else None,
        is_running=is_running,
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
    for r in top_res.data or []:
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


# ---------------------------------------------------------------------------
# Geocentric autopilot — auto-prepare (L0+L1+L2+L3) + per-candidate qualify
# ---------------------------------------------------------------------------
#
# UX model in /territorio:
#   1. First page visit -> POST /v1/territory/auto-prepare (idempotent).
#      The endpoint enqueues L0 (if no zones yet) and the L1+L2+L3 funnel
#      with a small budget so the candidate pool stays around the user's
#      target of ~10 final leads (30 L3 candidates -> ~10 qualify).
#   2. Operator inspects the L3 pool returned by /scan-results.
#   3. For each promising candidate the operator calls
#      /v1/territory/candidates/{id}/qualify which synchronously runs the
#      paid stages (L4 Solar API + L5 Haiku scoring + L6 lead creation).
#   4. /v1/territory/reset wipes the v3 pipeline state so the user can
#      restart the experiment from scratch.

# Default budget for the auto-prepare run. Tuned so we converge on roughly
# 10 actionable leads after the operator picks ~10 candidates from L3.
AUTO_PREPARE_MAX_L1 = 80
AUTO_PREPARE_PHASE = 3
QUALIFY_FINAL_TARGET = 10


class AutoPrepareResponse(BaseModel):
    job_id: str
    tenant_id: str
    enqueued_map: bool
    enqueued_funnel: bool
    zone_count: int
    candidate_count: int
    note: str


@router.post("/auto-prepare", response_model=AutoPrepareResponse, status_code=202)
async def auto_prepare(ctx: CurrentUser) -> AutoPrepareResponse:
    """Idempotently kick off the background prepare flow (L0 + L1+L2+L3).

    Safe to call from a useEffect on first /territorio visit:
      * If zones are missing -> enqueue map_target_areas_task.
      * If candidates are missing AND zones exist -> enqueue
        hunter_funnel_v3_task with `max_phase=3` and a small budget.
      * If both already populated -> no-op.

    The paid stages (Solar + Haiku + lead creation) are *never* triggered
    by this endpoint. They run on demand, one candidate at a time, via
    /v1/territory/candidates/{id}/qualify.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    wgs, provs = _resolve_sorgente_defaults(sb, tenant_id)
    if not wgs or not provs:
        raise HTTPException(
            status_code=409,
            detail=(
                "Configura prima settori e province nel modulo Sorgente. "
                "Impossibile preparare il territorio senza target."
            ),
        )

    zones_res = (
        sb.table("tenant_target_areas")
        .select("id", count="exact")
        .eq("tenant_id", tenant_id)
        .eq("status", "active")
        .execute()
    )
    zone_count = zones_res.count or 0

    enqueued_map = False
    if zone_count == 0:
        await enqueue(
            "map_target_areas_task",
            {
                "tenant_id": tenant_id,
                "wizard_groups": wgs,
                "province_codes": provs,
            },
            job_id=f"map_target_areas:{tenant_id}",
        )
        enqueued_map = True

    cand_res = (
        sb.table("scan_candidates")
        .select("id", count="exact")
        .eq("tenant_id", tenant_id)
        .eq("funnel_version", 3)
        .execute()
    )
    candidate_count = cand_res.count or 0

    enqueued_funnel = False
    note = ""
    if zone_count == 0:
        note = "Mappatura zone in corso. Riprova fra qualche minuto per avviare la scansione candidati."
    elif candidate_count == 0:
        await enqueue(
            "hunter_funnel_v3_task",
            {
                "tenant_id": tenant_id,
                "max_l1_candidates": AUTO_PREPARE_MAX_L1,
                "max_phase": AUTO_PREPARE_PHASE,
            },
            job_id=f"funnel_v3_autoprep:{tenant_id}:{int(time.time())}",
        )
        enqueued_funnel = True
        note = "Preparazione candidati in corso (L1→L3 senza costi API a pagamento)."
    else:
        note = (
            f"Pool già pronto: {candidate_count} candidati disponibili per la qualifica selettiva."
        )

    return AutoPrepareResponse(
        job_id=f"autoprep:{tenant_id}:{int(time.time())}",
        tenant_id=tenant_id,
        enqueued_map=enqueued_map,
        enqueued_funnel=enqueued_funnel,
        zone_count=zone_count,
        candidate_count=candidate_count,
        note=note,
    )


class QualifyCandidateResponse(BaseModel):
    candidate_id: str
    tenant_id: str
    solar_verdict: str | None
    overall_score: int | None
    recommended_for_rendering: bool
    lead_id: str | None
    qualified_count: int
    target_total: int
    cap_reached: bool
    message: str


@router.post(
    "/candidates/{candidate_id}/qualify",
    response_model=QualifyCandidateResponse,
)
async def qualify_candidate(
    ctx: CurrentUser,
    candidate_id: UUID = Path(..., description="scan_candidates.id (must belong to caller tenant)"),
) -> QualifyCandidateResponse:
    """Run L4 Solar + L5 Haiku + L6 lead creation for a single candidate.

    Synchronous: the operator has clicked a button and expects an answer
    within seconds (Solar ~2s, Haiku ~3s, lead insert <1s).

    Cap: refuses to run when the tenant already has QUALIFY_FINAL_TARGET
    funnel-v3 leads — the geocentric pilot deliberately stays small.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # Hard cap so the pilot never overruns the "circa 10 contatti finali"
    # target the operator asked for.
    qualified_res = (
        sb.table("leads")
        .select("id, subjects:subjects(raw_data)")
        .eq("tenant_id", tenant_id)
        .execute()
    )
    qualified_count = 0
    for lr in qualified_res.data or []:
        sub = lr.get("subjects") or {}
        raw = (sub.get("raw_data") or {}) if isinstance(sub, dict) else {}
        if raw.get("source") == "funnel_v3":
            qualified_count += 1
    if qualified_count >= QUALIFY_FINAL_TARGET:
        return QualifyCandidateResponse(
            candidate_id=str(candidate_id),
            tenant_id=tenant_id,
            solar_verdict=None,
            overall_score=None,
            recommended_for_rendering=False,
            lead_id=None,
            qualified_count=qualified_count,
            target_total=QUALIFY_FINAL_TARGET,
            cap_reached=True,
            message=(
                f"Cap raggiunto: già {qualified_count} contatti finali "
                f"qualificati (target {QUALIFY_FINAL_TARGET}). "
                "Usa /reset per ripartire."
            ),
        )

    # Load the candidate row + verify tenant ownership.
    sc_res = (
        sb.table("scan_candidates")
        .select("*")
        .eq("id", str(candidate_id))
        .eq("tenant_id", tenant_id)
        .eq("funnel_version", 3)
        .maybe_single()
        .execute()
    )
    sc = sc_res.data
    if not sc:
        raise HTTPException(404, detail="Candidato non trovato per questo tenant.")
    if (sc.get("building_quality_score") or 0) < 3:
        raise HTTPException(
            409,
            detail="Candidato non ha superato L3 (qualità edificio). Non eleggibile per qualifica.",
        )

    # Reconstruct the dataclass chain L1→L3 from the persisted row so we can
    # call the paid stages directly without re-running scraping.
    from ..agents.hunter_funnel.level4_solar_qualify import run_level4_solar_qualify
    from ..agents.hunter_funnel.level5_proxy_score import run_level5_proxy_score
    from ..agents.hunter_funnel.level6_promote_to_leads import run_level6_promote_to_leads
    from ..agents.hunter_funnel.types_v3 import (
        ContactExtraction,
        FunnelV3Context,
        PlaceCandidateRecord,
        QualifiedCandidate,
        ScrapedSignals,
    )
    from ..services.scan_cost_tracker import ScanCostAccumulator
    from ..services.tenant_config_service import get_tenant_config

    place_blob = (sc.get("enrichment") or {}).get("places") or {}
    scraped_blob = sc.get("scraped_data") or {}
    contact_blob = sc.get("contact_extraction") or {}

    record = PlaceCandidateRecord(
        candidate_id=UUID(sc["id"]),
        google_place_id=sc.get("google_place_id") or "",
        display_name=sc.get("business_name") or place_blob.get("display_name"),
        formatted_address=place_blob.get("formatted_address") or sc.get("hq_address"),
        lat=float(place_blob.get("lat") or sc.get("hq_lat") or 0.0),
        lng=float(place_blob.get("lng") or sc.get("hq_lng") or 0.0),
        types=list(place_blob.get("types") or []),
        business_status=place_blob.get("business_status"),
        user_ratings_total=place_blob.get("user_ratings_total"),
        rating=place_blob.get("rating"),
        website=place_blob.get("website"),
        phone=place_blob.get("phone"),
        google_maps_uri=place_blob.get("google_maps_uri"),
        zone_id=None,
        predicted_sector=sc.get("predicted_sector"),
        sector_confidence=sc.get("sector_confidence"),
        discovery_keyword=None,
    )

    scraped = ScrapedSignals(
        website_emails=list((scraped_blob.get("website") or {}).get("emails") or []),
        website_phone=(scraped_blob.get("website") or {}).get("phone"),
        website_pec=(scraped_blob.get("website") or {}).get("pec"),
        site_signals=list(scraped_blob.get("site_signals") or []),
        scrape_ok=bool(scraped_blob.get("scrape_ok")),
    )
    contact = ContactExtraction(
        best_email=contact_blob.get("best_email"),
        best_email_confidence=contact_blob.get("best_email_confidence"),
        best_email_type=contact_blob.get("best_email_type"),
        best_phone=contact_blob.get("best_phone"),
        pec=contact_blob.get("pec"),
        decision_maker_name=contact_blob.get("decision_maker_name"),
    )
    qc = QualifiedCandidate(
        record=record,
        scraped=scraped,
        contact=contact,
        building_quality_score=int(sc.get("building_quality_score") or 0),
    )

    # Build a one-shot funnel context. Reuse the existing scan_id when
    # available so the cost log lines up with the auto-prepare run that
    # discovered this candidate.
    config = await get_tenant_config(tenant_id)
    scan_id = sc.get("scan_id") or str(UUID(int=0))
    costs = ScanCostAccumulator(
        tenant_id=tenant_id,
        scan_id=scan_id,
        scan_mode="v3_qualify_one",
        territory_id=None,
    )
    funnel_ctx = FunnelV3Context(
        tenant_id=tenant_id,
        scan_id=scan_id,
        config=config,
        costs=costs,
        max_l1_candidates=1,
    )

    l4 = await run_level4_solar_qualify(funnel_ctx, [qc])
    accepted = [c for c in l4 if c.solar_verdict == "accepted"]
    if not accepted:
        await costs.flush(completed=True)
        verdict = l4[0].solar_verdict if l4 else "unknown"
        return QualifyCandidateResponse(
            candidate_id=str(candidate_id),
            tenant_id=tenant_id,
            solar_verdict=verdict,
            overall_score=None,
            recommended_for_rendering=False,
            lead_id=None,
            qualified_count=qualified_count,
            target_total=QUALIFY_FINAL_TARGET,
            cap_reached=False,
            message=f"Solar API: {verdict}. Nessun lead creato.",
        )

    l5 = await run_level5_proxy_score(funnel_ctx, accepted)
    inserted = await run_level6_promote_to_leads(funnel_ctx, l5)
    await costs.flush(completed=True)

    scored = l5[0] if l5 else None
    overall = int(scored.overall_score) if scored else None
    recommended = bool(scored.recommended_for_rendering) if scored else False

    lead_id: str | None = None
    if inserted > 0:
        lead_lookup = (
            sb.table("leads")
            .select("id, subjects:subjects(raw_data)")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        for lr in lead_lookup.data or []:
            sub = lr.get("subjects") or {}
            raw = (sub.get("raw_data") or {}) if isinstance(sub, dict) else {}
            if raw.get("scan_candidate_id") == str(candidate_id):
                lead_id = lr["id"]
                break

    return QualifyCandidateResponse(
        candidate_id=str(candidate_id),
        tenant_id=tenant_id,
        solar_verdict="accepted",
        overall_score=overall,
        recommended_for_rendering=recommended,
        lead_id=lead_id,
        qualified_count=qualified_count + (1 if lead_id else 0),
        target_total=QUALIFY_FINAL_TARGET,
        cap_reached=False,
        message=(
            f"Lead creato (score {overall})."
            if lead_id
            else f"Score {overall}: candidato sotto soglia, nessun lead creato."
        ),
    )


class ResetResponse(BaseModel):
    tenant_id: str
    candidates_deleted: int
    leads_deleted: int
    cost_logs_deleted: int


@router.post("/reset", response_model=ResetResponse)
async def reset_pipeline(ctx: CurrentUser) -> ResetResponse:
    """Wipe the v3 funnel state for this tenant so the operator can restart.

    Removes:
      * scan_candidates rows (funnel_version=3) for this tenant.
      * scan_cost_log rows (scan_mode in {v3_funnel, v3_qualify_one}).
      * leads + subjects created via funnel_v3 (raw_data.source='funnel_v3').

    Does NOT delete tenant_target_areas — those are produced by L0 OSM
    mapping and don't need to be re-scraped on every reset.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    cands_before = (
        sb.table("scan_candidates")
        .select("id", count="exact")
        .eq("tenant_id", tenant_id)
        .eq("funnel_version", 3)
        .execute()
    )
    cand_count = cands_before.count or 0
    if cand_count:
        sb.table("scan_candidates").delete().eq("tenant_id", tenant_id).eq(
            "funnel_version", 3
        ).execute()

    cost_before = (
        sb.table("scan_cost_log")
        .select("scan_id", count="exact")
        .eq("tenant_id", tenant_id)
        .in_("scan_mode", ["v3_funnel", "v3_qualify_one"])
        .execute()
    )
    cost_count = cost_before.count or 0
    if cost_count:
        sb.table("scan_cost_log").delete().eq("tenant_id", tenant_id).in_(
            "scan_mode", ["v3_funnel", "v3_qualify_one"]
        ).execute()

    leads_deleted = 0
    leads_q = (
        sb.table("leads")
        .select("id, subject_id, subjects:subjects(raw_data)")
        .eq("tenant_id", tenant_id)
        .execute()
    )
    for lr in leads_q.data or []:
        sub = lr.get("subjects") or {}
        raw = (sub.get("raw_data") or {}) if isinstance(sub, dict) else {}
        if raw.get("source") == "funnel_v3":
            try:
                sb.table("leads").delete().eq("id", lr["id"]).execute()
                if lr.get("subject_id"):
                    sb.table("subjects").delete().eq("id", lr["subject_id"]).execute()
                leads_deleted += 1
            except Exception:  # noqa: BLE001
                pass

    return ResetResponse(
        tenant_id=tenant_id,
        candidates_deleted=cand_count,
        leads_deleted=leads_deleted,
        cost_logs_deleted=cost_count,
    )
