"""Admin-only endpoints (super-admin).

All routes in this module require ``ctx.role == 'super_admin'``.
The service role client bypasses RLS so these endpoints can reach
cross-tenant data — the role gate is the only thing keeping them
safe. Do not expose any new admin endpoint without calling
``_require_super_admin(ctx)`` at the top.

Surface area:

    GET    /system/health                    — smoke check
    GET    /system/stats                     — platform KPIs
    GET    /blacklist                        — global email blacklist
    GET    /tenants                          — all tenants + counters
    GET    /tenants/{id}                     — one tenant + usage
    PATCH  /tenants/{id}                     — tier/status/flags updates
    GET    /tenants/{id}/feature-flags       — current flags
    PATCH  /tenants/{id}/feature-flags       — update one flag (partial)
    GET    /cost-report?days=30              — spend rollup platform-wide
    POST   /seed-test-candidate              — inject synthetic company, run full pipeline
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import geohash  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..agents.scoring import ScoringAgent, ScoringInput
from ..core.queue import enqueue
from ..core.security import CurrentUser
from ..core.supabase_client import get_service_client
from ..models.enums import RoofDataSource, RoofStatus, SubjectType
from ..services.territory_lock_service import unlock as territory_unlock

router = APIRouter()


# ---------------------------------------------------------------------------
# Role gate
# ---------------------------------------------------------------------------


def _require_super_admin(ctx: CurrentUser) -> None:
    if ctx.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires super_admin role",
        )


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


@router.get("/system/health")
async def system_health(ctx: CurrentUser) -> dict[str, object]:
    _require_super_admin(ctx)
    return {"status": "ok", "services": ["db", "redis", "claude", "replicate"]}


@router.get("/system/stats")
async def system_stats(ctx: CurrentUser) -> dict[str, Any]:
    """Platform-wide KPIs. Counts are cheap under RLS-bypassing service role."""
    _require_super_admin(ctx)
    sb = get_service_client()

    tenants = sb.table("tenants").select("id", count="exact", head=True).execute()
    active_tenants = (
        sb.table("tenants")
        .select("id", count="exact", head=True)
        .eq("status", "active")
        .execute()
    )
    leads = sb.table("leads").select("id", count="exact", head=True).execute()
    users = (
        sb.table("tenant_members")
        .select("user_id", count="exact", head=True)
        .execute()
    )
    return {
        "tenants_total": tenants.count or 0,
        "tenants_active": active_tenants.count or 0,
        "users_total": users.count or 0,
        "leads_total": leads.count or 0,
    }


# ---------------------------------------------------------------------------
# Blacklist
# ---------------------------------------------------------------------------


@router.get("/blacklist")
async def list_blacklist(ctx: CurrentUser) -> list[dict[str, object]]:
    _require_super_admin(ctx)
    sb = get_service_client()
    res = (
        sb.table("global_blacklist")
        .select("*")
        .order("created_at", desc=True)
        .limit(500)
        .execute()
    )
    return res.data or []


# ---------------------------------------------------------------------------
# Tenant management
# ---------------------------------------------------------------------------


@router.get("/tenants")
async def list_tenants(ctx: CurrentUser) -> list[dict[str, Any]]:
    """All tenants + roll-up counters (leads, cost MTD, members)."""
    _require_super_admin(ctx)
    sb = get_service_client()
    try:
        res = sb.rpc("admin_tenant_overview", {}).execute()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"admin_tenant_overview rpc failed: {exc}"
        ) from exc
    return res.data or []


@router.get("/tenants/{tenant_id}")
async def get_tenant(ctx: CurrentUser, tenant_id: str) -> dict[str, Any]:
    _require_super_admin(ctx)
    sb = get_service_client()
    t_res = (
        sb.table("tenants")
        .select("*")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    if not t_res.data:
        raise HTTPException(status_code=404, detail="tenant not found")
    usage_res = sb.rpc(
        "analytics_usage_mtd", {"p_tenant_id": tenant_id}
    ).execute()
    members_res = (
        sb.table("tenant_members")
        .select("user_id, role, created_at")
        .eq("tenant_id", tenant_id)
        .execute()
    )
    return {
        "tenant": t_res.data[0],
        "usage_mtd": usage_res.data,
        "members": members_res.data or [],
    }


class TenantAdminUpdate(BaseModel):
    """Super-admin editable tenant fields — distinct from tenant self-serve."""

    tier: Literal["founding", "growth", "enterprise"] | None = None
    status: Literal[
        "onboarding", "active", "paused", "churned", "trial"
    ] | None = None
    monthly_rate_cents: int | None = Field(default=None, ge=0)
    contract_start_date: str | None = None
    contract_end_date: str | None = None
    business_name: str | None = None


@router.patch("/tenants/{tenant_id}")
async def update_tenant(
    ctx: CurrentUser, tenant_id: str, payload: TenantAdminUpdate
) -> dict[str, Any]:
    _require_super_admin(ctx)
    update = payload.model_dump(exclude_none=True)
    if not update:
        raise HTTPException(status_code=400, detail="no updatable fields")
    sb = get_service_client()
    res = (
        sb.table("tenants")
        .update(update)
        .eq("id", tenant_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="tenant not found")
    return res.data[0]


# ---------------------------------------------------------------------------
# Territory lock override (ops-only escape hatch)
# ---------------------------------------------------------------------------


@router.post("/tenants/{tenant_id}/territory-unlock")
async def unlock_territory(ctx: CurrentUser, tenant_id: str) -> dict[str, Any]:
    """Clear the territorial exclusivity lock for a tenant.

    This is the only way to reverse a `territory-confirm`. Reserved
    for ops because unlocking a tenant effectively re-opens a signed
    commercial commitment. Audit trail lives in the application logs
    (`territory_lock.unset` with tenant_id).
    """
    _require_super_admin(ctx)
    row = territory_unlock(tenant_id)
    return {
        "tenant_id": tenant_id,
        "territory_locked_at": row.get("territory_locked_at"),
        "territory_locked_by": row.get("territory_locked_by"),
    }


# ---------------------------------------------------------------------------
# Feature flags (stored in tenants.settings.feature_flags JSONB map)
# ---------------------------------------------------------------------------


@router.get("/tenants/{tenant_id}/feature-flags")
async def get_feature_flags(ctx: CurrentUser, tenant_id: str) -> dict[str, Any]:
    _require_super_admin(ctx)
    sb = get_service_client()
    res = (
        sb.table("tenants")
        .select("settings")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="tenant not found")
    settings_obj = res.data[0].get("settings") or {}
    return {"feature_flags": settings_obj.get("feature_flags", {})}


class FeatureFlagPatch(BaseModel):
    """Partial update — only the keys provided are changed/added."""

    flags: dict[str, Any] = Field(
        description=(
            "Map of flag name → value. Null value removes the flag. "
            "Example: {\"whatsapp_outreach\": true, \"legacy_scoring\": null}"
        )
    )


@router.patch("/tenants/{tenant_id}/feature-flags")
async def patch_feature_flags(
    ctx: CurrentUser, tenant_id: str, payload: FeatureFlagPatch
) -> dict[str, Any]:
    _require_super_admin(ctx)
    sb = get_service_client()

    res = (
        sb.table("tenants")
        .select("settings")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="tenant not found")

    current = res.data[0].get("settings") or {}
    flags = dict(current.get("feature_flags") or {})
    for key, value in payload.flags.items():
        if value is None:
            flags.pop(key, None)
        else:
            flags[key] = value

    new_settings = dict(current)
    new_settings["feature_flags"] = flags

    upd = (
        sb.table("tenants")
        .update({"settings": new_settings})
        .eq("id", tenant_id)
        .execute()
    )
    return {"tenant_id": tenant_id, "feature_flags": flags, "updated": bool(upd.data)}


# ---------------------------------------------------------------------------
# Cost report — platform-wide spend rollup
# ---------------------------------------------------------------------------


@router.get("/cost-report")
async def cost_report(
    ctx: CurrentUser,
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    _require_super_admin(ctx)
    sb = get_service_client()
    try:
        res = sb.rpc("admin_platform_cost", {"p_days": days}).execute()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"admin_platform_cost rpc failed: {exc}"
        ) from exc
    return res.data or {
        "window_days": days,
        "by_tenant": [],
        "by_provider": [],
        "total_cost_cents": 0,
    }


# ---------------------------------------------------------------------------
# Test candidate injection — end-to-end pipeline smoke-test
# ---------------------------------------------------------------------------


class SolarOverride(BaseModel):
    """Optional Solar API override — skips the real Google Solar call."""

    annual_kwh: float = Field(default=45000.0, ge=0)
    roof_area_m2: float = Field(default=180.0, ge=0)
    orientation: str = Field(default="south")
    estimated_kwp: float = Field(default=30.0, ge=0)
    shading_score: float = Field(default=0.85, ge=0, le=1)


class SeedTestCandidateRequest(BaseModel):
    """Shape for POST /v1/admin/seed-test-candidate.

    Mirrors ``AtokaProfile`` from ``services.italian_business_service``
    plus a few test-control fields. All address fields are required so
    we can build a stable geohash and a synthetic ``subjects`` row that
    downstream agents can consume without calling Atoka.
    """

    tenant_id: str = Field(description="Target tenant UUID")

    # ── Atoka-equivalent fields ──────────────────────────────────────────
    vat_number: str = Field(min_length=5, max_length=30)
    legal_name: str = Field(min_length=1, max_length=255)
    ateco_code: str | None = Field(default=None)
    ateco_description: str | None = Field(default=None)
    yearly_revenue_cents: int | None = Field(default=None, ge=0)
    employees: int | None = Field(default=None, ge=0)
    website_domain: str | None = Field(default=None)

    # ── HQ address (required for geo + Solar) ────────────────────────────
    hq_address: str = Field(min_length=1)
    hq_cap: str = Field(min_length=4, max_length=10)
    hq_city: str = Field(min_length=1)
    hq_province: str = Field(min_length=2, max_length=2)
    hq_lat: float = Field(description="Decimal latitude — required; avoids Mapbox geocode")
    hq_lng: float = Field(description="Decimal longitude — required; avoids Mapbox geocode")

    # ── Decision-maker (goes into subjects + email personalisation) ───────
    decision_maker_name: str | None = Field(default=None)
    decision_maker_role: str | None = Field(default=None)
    decision_maker_email: str | None = Field(
        default=None,
        description="Real inbox — this is the recipient of the test email",
    )

    # ── Test-control flags ────────────────────────────────────────────────
    solar_override: SolarOverride = Field(
        default_factory=SolarOverride,
        description="Skip Google Solar API — use these synthetic values instead",
    )
    run_outreach: bool = Field(
        default=True,
        description="Enqueue outreach_task (sends real email). Set false to stop after creative render.",
    )


class SeedTestCandidateResponse(BaseModel):
    ok: bool = True
    roof_id: str
    subject_id: str
    scoring_job_id: str
    creative_job_id: str
    outreach_job_id: str | None = None
    message: str


@router.post(
    "/seed-test-candidate",
    response_model=SeedTestCandidateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def seed_test_candidate(
    ctx: CurrentUser, body: SeedTestCandidateRequest
) -> SeedTestCandidateResponse:
    """Inject a synthetic company into the pipeline for end-to-end testing.

    Bypasses Atoka L1, Places L2, Claude L3, and Google Solar L4 by
    inserting rows directly into ``roofs`` + ``subjects``, then enqueuing
    the downstream tasks (scoring → creative → outreach) with time-spaced
    ``defer_until`` so each step has room to complete before the next fires.

    Timeline (approximate):
      • t+0s   — scoring_task  → creates ``leads`` row, sets score/tier
      • t+45s  — creative_task → renders Remotion MP4+GIF, uploads to storage
      • t+3min — outreach_task → sends email via Resend on verified domain

    The endpoint is idempotent within a tenant for the same ``vat_number``:
    re-running with the same P.IVA upserts the roof (geohash unique) and
    subject (roof_id unique) rather than creating duplicates.
    """
    _require_super_admin(ctx)

    sb = get_service_client()
    now = datetime.now(timezone.utc)
    ov = body.solar_override

    # ── 1. Upsert roof row ────────────────────────────────────────────────
    # geohash precision=9 gives ~4m resolution — same as level4_solar_gate.
    gh = geohash.encode(body.hq_lat, body.hq_lng, precision=9)

    roof_payload: dict[str, Any] = {
        "tenant_id": body.tenant_id,
        "lat": body.hq_lat,
        "lng": body.hq_lng,
        "geohash": gh,
        "address": body.hq_address,
        "cap": body.hq_cap,
        "comune": body.hq_city,
        "provincia": body.hq_province,
        "area_sqm": ov.roof_area_m2,
        "estimated_kwp": ov.estimated_kwp,
        "estimated_yearly_kwh": ov.annual_kwh,
        "exposure": ov.orientation,
        "shading_score": ov.shading_score,
        "has_existing_pv": False,
        "data_source": RoofDataSource.GOOGLE_SOLAR.value,
        "classification": SubjectType.B2B.value,
        "status": RoofStatus.DISCOVERED.value,
        "scan_cost_cents": 0,
        "raw_data": {
            "seed_test": True,
            "vat_number": body.vat_number,
            "inserted_at": now.isoformat(),
        },
    }

    roof_res = (
        sb.table("roofs")
        .upsert(roof_payload, on_conflict="tenant_id,geohash")
        .execute()
    )
    if not roof_res.data:
        raise HTTPException(status_code=502, detail="Failed to upsert roof row")
    roof_id: str = roof_res.data[0]["id"]

    # ── 2. Upsert subject row ─────────────────────────────────────────────
    # pii_hash = SHA256 of "legal_name|vat_number" (normalised lowercase).
    pii_raw = f"{body.legal_name.lower().strip()}|{body.vat_number.lower().strip()}"
    pii_hash = hashlib.sha256(pii_raw.encode()).hexdigest()

    subject_payload: dict[str, Any] = {
        "tenant_id": body.tenant_id,
        "roof_id": roof_id,
        "type": SubjectType.B2B.value,
        "business_name": body.legal_name,
        "vat_number": body.vat_number,
        "ateco_code": body.ateco_code,
        "ateco_description": body.ateco_description,
        "yearly_revenue_cents": body.yearly_revenue_cents,
        "employees": body.employees,
        "decision_maker_name": body.decision_maker_name,
        "decision_maker_role": body.decision_maker_role,
        "decision_maker_email": body.decision_maker_email,
        "data_sources": ["seed_test"],
        "enrichment_cost_cents": 0,
        "enrichment_completed_at": now.isoformat(),
        "pii_hash": pii_hash,
    }

    subject_res = (
        sb.table("subjects")
        .upsert(subject_payload, on_conflict="tenant_id,roof_id")
        .execute()
    )
    if not subject_res.data:
        raise HTTPException(status_code=502, detail="Failed to upsert subject row")
    subject_id: str = subject_res.data[0]["id"]

    # ── 3. Run scoring synchronously — we need the lead_id for downstream ──
    # ScoringAgent creates (or updates) the ``leads`` row and returns its id.
    scoring_out = await ScoringAgent().run(
        ScoringInput(
            tenant_id=body.tenant_id,
            roof_id=roof_id,
            subject_id=subject_id,
        )
    )
    lead_id: str | None = scoring_out.lead_id
    if not lead_id:
        raise HTTPException(
            status_code=502,
            detail="Scoring agent ran but returned no lead_id — check worker logs",
        )

    # ── 4. Enqueue creative_task (deferred 5s — scoring row propagates) ───
    # Remotion render can take 60-120s; we just kick it off and return.
    # Non-fatal: if Redis is unreachable (e.g. env var not set in this env)
    # we still return the scoring result so the test panel doesn't hang.
    creative_job_id: str = ""
    try:
        creative_job = await enqueue(
            "creative_task",
            {"tenant_id": body.tenant_id, "lead_id": lead_id, "force": True},
            job_id=f"creative:{body.tenant_id}:{lead_id}",
            defer_until=now + timedelta(seconds=5),
        )
        creative_job_id = creative_job.get("job_id", "")
    except Exception as exc:  # noqa: BLE001
        log.warning("creative_enqueue_failed", lead_id=lead_id, error=str(exc))
        creative_job_id = f"redis_unavailable:{exc}"

    # ── 5. Enqueue outreach_task (deferred 3min — creative needs to finish) ─
    outreach_job_id: str | None = None
    if body.run_outreach:
        try:
            outreach_job = await enqueue(
                "outreach_task",
                {
                    "tenant_id": body.tenant_id,
                    "lead_id": lead_id,
                    "channel": "email",
                    "force": True,
                },
                job_id=f"outreach_seed:{body.tenant_id}:{lead_id}",
                defer_until=now + timedelta(minutes=3),
            )
            outreach_job_id = outreach_job.get("job_id")
        except Exception as exc:  # noqa: BLE001
            log.warning("outreach_enqueue_failed", lead_id=lead_id, error=str(exc))
            outreach_job_id = f"redis_unavailable:{exc}"

    redis_warn = (
        " ⚠️ Redis non raggiungibile — job in coda non garantiti."
        if "redis_unavailable" in creative_job_id
        else ""
    )
    return SeedTestCandidateResponse(
        roof_id=roof_id,
        subject_id=subject_id,
        scoring_job_id=f"inline:score={scoring_out.score},tier={scoring_out.tier}",
        creative_job_id=creative_job_id,
        outreach_job_id=outreach_job_id,
        message=(
            f"Scored {scoring_out.score}/100 ({scoring_out.tier}). "
            "creative ~5s, "
            + (f"email to {body.decision_maker_email} ~3min."
               if body.run_outreach else "outreach skipped.")
            + redis_warn
        ),
    )
