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
    POST   /demo/reset-attempts             — reset demo pipeline attempt counter for a tenant
    GET    /demo/runs                        — all demo pipeline runs (cross-tenant, super_admin)
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal

import geohash  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..agents.creative import CreativeAgent, CreativeInput
from ..agents.outreach import OutreachAgent, OutreachInput
from ..agents.scoring import ScoringAgent, ScoringInput
from ..core.logging import get_logger
from ..core.queue import enqueue  # noqa: F401 — retained for non-test admin paths
from ..core.security import CurrentUser
from ..core.supabase_client import get_service_client
from ..models.enums import OutreachChannel, RoofDataSource, RoofStatus, SubjectType
from ..services.territory_lock_service import unlock as territory_unlock

log = get_logger(__name__)
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
    return {"status": "ok", "services": ["db", "redis", "claude", "google_solar"]}


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
        log.warning("admin.tenant_overview_failed", err=str(exc))
        raise HTTPException(
            status_code=502,
            detail="Dati tenant temporaneamente non disponibili. Riprova tra qualche minuto.",
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

    # Sprint 11 — warehouse pipeline knobs. Validated by the DB CHECK
    # constraints in migration 0072 (send_cap bounds + warehouse window
    # sanity), so we only do shallow type-level validation here.
    daily_target_send_cap: int | None = Field(default=None, ge=1, le=5000)
    daily_send_cap_min: int | None = Field(default=None, ge=1, le=5000)
    daily_send_cap_max: int | None = Field(default=None, ge=1, le=5000)
    warehouse_buffer_days: int | None = Field(default=None, ge=1, le=30)
    lead_expiration_days: int | None = Field(default=None, ge=1, le=90)
    atoka_survival_target: float | None = Field(default=None, ge=0.10, le=1.00)


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
        log.warning("admin.platform_cost_failed", err=str(exc))
        raise HTTPException(
            status_code=502,
            detail="Dati di costo piattaforma temporaneamente non disponibili. Riprova tra qualche minuto.",
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
    decision_maker_phone: str | None = Field(
        default=None,
        description="Optional phone number for the anagrafica panel (manual override).",
    )

    # ── Test-control flags ────────────────────────────────────────────────
    solar_override: SolarOverride = Field(
        default_factory=SolarOverride,
        description="Skip Google Solar API — use these synthetic values instead",
    )
    run_outreach: bool = Field(
        default=True,
        description="Run outreach (sends real email). Always executed synchronously — no worker needed.",
    )


class SeedTestCandidateResponse(BaseModel):
    ok: bool = True
    roof_id: str
    subject_id: str
    scoring_job_id: str
    creative_job_id: str
    outreach_job_id: str | None = None
    outreach_result: str | None = None
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
    inserting rows directly into ``roofs`` + ``subjects``, then running the
    downstream pipeline (scoring → creative → outreach) **synchronously**
    in-request so the test panel gets a single deterministic result and
    doesn't depend on a running arq worker.

    Request timeline (blocking):
      • scoring  → creates ``leads`` row, sets score/tier (~1s)
      • creative → Mapbox tile + Replicate AI + Remotion render (~60-120s)
      • outreach → sends email via the configured provider (~2s)

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
        # Mark the email as verified for test candidates — the operator
        # explicitly supplied it, so NeverBounce gating would be redundant.
        # Without this, _resolve_recipient() returns None and the outreach
        # agent skips with reason='no_verified_email'.
        "decision_maker_email_verified": bool(body.decision_maker_email),
        "decision_maker_phone": body.decision_maker_phone,
        "decision_maker_phone_source": "manual" if body.decision_maker_phone else None,
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

    # ── 4. Run creative synchronously so the email below has a hero image ──
    # Why sync: the test endpoint must produce an email with the rendered
    # roof in the body. If creative were enqueued, OutreachAgent would run
    # before the worker (if any) completed the render, and the email would
    # ship without the hero image / GIF / video URL.
    # Remotion render can take 60-120s — the HTTP request will block that
    # long. Acceptable for a manual admin smoke-test.
    creative_job_id: str = ""
    try:
        creative_out = await CreativeAgent().run(
            CreativeInput(
                tenant_id=body.tenant_id,
                lead_id=lead_id,
                force=True,
            )
        )
        if creative_out.skipped:
            creative_job_id = f"skipped:{creative_out.reason}"
            log.warning(
                "seed_test.creative_skipped",
                lead_id=lead_id,
                reason=creative_out.reason,
            )
        else:
            creative_job_id = (
                f"inline:after={'y' if creative_out.after_url else 'n'},"
                f"gif={'y' if creative_out.gif_url else 'n'},"
                f"mp4={'y' if creative_out.video_url else 'n'}"
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("seed_test.creative_error", lead_id=lead_id, error=str(exc))
        creative_job_id = f"error:{str(exc)[:120]}"

    # ── 5. Run outreach synchronously (no worker needed for testing) ─────────
    # Rather than enqueueing and waiting for a worker that may not be running,
    # we call OutreachAgent directly. `force=True` bypasses already_sent and
    # GDPR footer guards so the test always fires regardless of prior state.
    outreach_job_id: str | None = None
    outreach_result: str | None = None
    if body.run_outreach:
        try:
            outreach_out = await OutreachAgent().run(
                OutreachInput(
                    tenant_id=body.tenant_id,
                    lead_id=lead_id,
                    channel=OutreachChannel.EMAIL,
                    sequence_step=1,
                    force=True,
                )
            )
            if outreach_out.skipped:
                outreach_result = f"skipped: {outreach_out.reason}"
                outreach_job_id = f"skipped:{outreach_out.reason}"
                log.warning(
                    "seed_test.outreach_skipped",
                    lead_id=lead_id,
                    reason=outreach_out.reason,
                )
            elif outreach_out.status == "failed":
                outreach_result = f"failed: {outreach_out.reason}"
                outreach_job_id = f"failed:{outreach_out.reason}"
                log.warning(
                    "seed_test.outreach_failed",
                    lead_id=lead_id,
                    reason=outreach_out.reason,
                )
            else:
                outreach_result = f"sent to {body.decision_maker_email}"
                outreach_job_id = f"inline:sent:{outreach_out.provider_id or 'ok'}"
        except Exception as exc:  # noqa: BLE001
            log.warning("seed_test.outreach_error", lead_id=lead_id, error=str(exc))
            outreach_result = f"error: {exc}"
            outreach_job_id = f"error:{str(exc)[:120]}"

    creative_warn = (
        f" ⚠️ Creative: {creative_job_id}."
        if creative_job_id.startswith(("skipped:", "error:"))
        else f" Creative: {creative_job_id}."
    )
    outreach_msg = (
        f" Outreach: {outreach_result}."
        if outreach_result
        else " outreach skipped."
    )
    return SeedTestCandidateResponse(
        roof_id=roof_id,
        subject_id=subject_id,
        scoring_job_id=f"inline:score={scoring_out.score},tier={scoring_out.tier}",
        creative_job_id=creative_job_id,
        outreach_job_id=outreach_job_id,
        outreach_result=outreach_result,
        message=(
            f"Scored {scoring_out.score}/100 ({scoring_out.tier})."
            + creative_warn
            + outreach_msg
        ),
    )


# ---------------------------------------------------------------------------
# Demo pipeline attempt counter — reset
# ---------------------------------------------------------------------------


class DemoResetAttemptsRequest(BaseModel):
    """POST /v1/admin/demo/reset-attempts

    Resets ``tenants.demo_pipeline_test_remaining`` for a demo tenant to
    ``count`` (default 999).  Use this during QA to avoid manually editing
    the counter in Supabase Studio between test runs.

    The endpoint intentionally does NOT toggle ``is_demo`` — that flag is
    managed via PATCH /v1/admin/tenants/{id}.  Resetting the counter on a
    non-demo tenant has no visible effect (the dashboard banner only renders
    when ``is_demo = true AND demo_pipeline_test_remaining > 0``).
    """

    tenant_id: str = Field(description="UUID of the tenant to reset")
    count: int = Field(
        default=999,
        ge=1,
        le=999,
        description="New value for demo_pipeline_test_remaining (1-999).",
    )


class DemoResetAttemptsResponse(BaseModel):
    ok: bool = True
    tenant_id: str
    attempts_remaining: int
    message: str


@router.post(
    "/demo/reset-attempts",
    response_model=DemoResetAttemptsResponse,
)
async def admin_demo_reset_attempts(
    ctx: CurrentUser, body: DemoResetAttemptsRequest
) -> DemoResetAttemptsResponse:
    """Reset the demo pipeline attempt counter for a tenant.

    Calls the ``demo_reset_pipeline_attempts`` SQL RPC (migration 0088)
    which sets ``demo_pipeline_test_remaining`` to ``body.count``
    unconditionally.

    Typical QA usage::

        POST /v1/admin/demo/reset-attempts
        { "tenant_id": "<uuid>" }

    Returns the post-update remaining count so the caller can confirm
    the change took effect.  Returns 404 when the tenant doesn't exist.
    """
    _require_super_admin(ctx)
    sb = get_service_client()

    try:
        res = sb.rpc(
            "demo_reset_pipeline_attempts",
            {"p_tenant_id": body.tenant_id, "p_new_count": body.count},
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.error(
            "admin.demo_reset_attempts_rpc_failed",
            tenant_id=body.tenant_id,
            err=str(exc),
        )
        raise HTTPException(
            status_code=502,
            detail="Failed to reset demo attempts counter via RPC.",
        ) from exc

    val = res.data
    # RPC returns NULL when no tenant row matched.
    if val is None:
        raise HTTPException(
            status_code=404,
            detail=f"Tenant {body.tenant_id!r} not found.",
        )
    # PostgREST may wrap the scalar in a list of dicts depending on version.
    if isinstance(val, list):
        if not val:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        row = val[0]
        remaining = row if isinstance(row, int) else row.get("demo_reset_pipeline_attempts")
    elif isinstance(val, dict):
        remaining = val.get("demo_reset_pipeline_attempts")
    else:
        remaining = int(val)

    if remaining is None:
        raise HTTPException(
            status_code=404,
            detail=f"Tenant {body.tenant_id!r} not found.",
        )

    log.info(
        "admin.demo_reset_attempts",
        tenant_id=body.tenant_id,
        new_count=remaining,
        reset_by=ctx.sub,
    )

    return DemoResetAttemptsResponse(
        tenant_id=body.tenant_id,
        attempts_remaining=remaining,
        message=f"Demo pipeline counter reset to {remaining} for tenant {body.tenant_id}.",
    )


# ---------------------------------------------------------------------------
# Demo pipeline runs — cross-tenant audit log
# ---------------------------------------------------------------------------


class DemoRunRow(BaseModel):
    id: str
    tenant_id: str
    tenant_name: str | None = None
    lead_id: str | None = None
    status: str
    failed_step: str | None = None
    error_message: str | None = None
    notes: str | None = None
    created_at: str
    updated_at: str
    # Email delivery status — derived from the most-recent ``outreach_sends``
    # row for the run's lead. Lets the dashboard tell the difference between
    # "Resend accepted the request" (sent) and "the recipient mailbox actually
    # received it" (delivered) — the gap that hid silent bounces and the
    # ``DEMO_EMAIL_RECIPIENT_OVERRIDE`` redirect from operators on demo calls.
    email_status: str | None = None              # SCHEDULED|SENT|DELIVERED|FAILED|...
    email_status_detail: str | None = None        # outreach_sends.failure_reason (bounce/complaint code)
    email_message_id: str | None = None           # for cross-checking against Resend dashboard
    email_sent_at: str | None = None              # ISO-8601, when status became SENT
    email_recipient: str | None = None            # the address the email was actually sent to
    # Roof identification provenance — lets the dashboard render a badge
    # showing whether the rendered roof was confirmed by Atoka, scraped, or
    # is just an HQ centroid (low confidence → review before demoing).
    roof_source: str | None = None                # subjects.sede_operativa_source
    roof_confidence: str | None = None            # high|medium|low|none


class DemoRunsResponse(BaseModel):
    runs: list[DemoRunRow]
    total: int


@router.get("/demo/runs", response_model=DemoRunsResponse)
async def admin_demo_runs(
    ctx: CurrentUser,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
) -> DemoRunsResponse:
    """List all demo pipeline runs across all tenants.

    Returns runs ordered newest-first.  Supports optional filtering by
    ``status`` (scoring|creative|outreach|done|failed) and ``tenant_id``.
    Used by the ``/admin/demo-runs`` dashboard page to give the operator
    a real-time view of every customer-facing test run.

    Any unhandled exception below the auth gate is caught at the bottom
    and surfaced as a 502 with the exception type/message in the body —
    this beats letting FastAPI's default 500 ("Internal Server Error",
    21 bytes) hide the cause when the dashboard goes red.
    """
    try:
        return await _admin_demo_runs_impl(
            ctx, limit=limit, offset=offset, status=status, tenant_id=tenant_id
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — last-mile catchall
        log.exception(
            "admin.demo_runs_unhandled",
            err_type=type(exc).__name__,
            err=str(exc)[:300],
        )
        raise HTTPException(
            status_code=502,
            detail=f"{type(exc).__name__}: {str(exc)[:200]}",
        ) from exc


async def _admin_demo_runs_impl(
    ctx: CurrentUser,
    *,
    limit: int,
    offset: int,
    status: str | None,
    tenant_id: str | None,
) -> DemoRunsResponse:
    """Inner implementation of ``admin_demo_runs``.

    Split out so the public endpoint can wrap the whole flow in one
    last-mile try/except without rewriting every code path. Tests can
    also hit this directly when they want to bypass the catchall.
    """
    _require_super_admin(ctx)
    sb = get_service_client()

    # --- Count (for pagination header) ---
    # Soft-fail: a count error never deserves to block the page —
    # at worst we render with ``total=0`` and the pagination footer
    # quietly disappears. Earlier this endpoint surfaced as an
    # opaque 500 when a transient PostgREST hiccup hit the count
    # query, which made the whole admin page unusable instead of
    # degrading gracefully.
    total: int = 0
    try:
        count_q = sb.table("demo_pipeline_runs").select(
            "id", count="exact", head=True
        )
        if status:
            count_q = count_q.eq("status", status)
        if tenant_id:
            count_q = count_q.eq("tenant_id", tenant_id)
        count_res = count_q.execute()
        total = count_res.count or 0
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "admin.demo_runs_count_failed",
            err_type=type(exc).__name__,
            err=str(exc)[:200],
        )

    # --- Rows ---
    # Two-step lookup instead of a single embedded-resource query.
    #
    # Earlier revisions of this endpoint piled the tenant + leads +
    # subjects join into one PostgREST embedded select. That broke
    # transparently in two distinct ways across the day:
    #   1. A column referenced in the embed didn't exist yet
    #      (sede_operativa_confidence — added by migration 0090).
    #   2. PostgREST schema cache hadn't picked up a column rename
    #      (decision_maker_email moved between tables in our heads
    #      before we double-checked the source-of-truth in outreach.py).
    #
    # In both cases the dashboard fell back to "Failed to query demo
    # runs" with no actionable signal. The ergonomics are awful: any
    # future schema drift on subjects or leads silently nukes the
    # whole admin page.
    #
    # Splitting into 2 round-trips trades a tiny amount of latency for
    # the ability to log + degrade per-step. The "rows" query stays
    # narrow on demo_pipeline_runs (+ tenant name only) and never
    # blocks on a join we don't strictly need; the subject / send
    # lookups happen below as best-effort batched ``in`` queries.
    q = (
        sb.table("demo_pipeline_runs")
        .select(
            "id, tenant_id, lead_id, status, failed_step, "
            "error_message, notes, created_at, updated_at, "
            "tenants!inner(business_name)"
        )
        .order("created_at", desc=True)
        .limit(limit)
        .offset(offset)
    )
    if status:
        q = q.eq("status", status)
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)

    try:
        res = q.execute()
    except Exception as exc:  # noqa: BLE001
        log.error(
            "admin.demo_runs_query_failed",
            err_type=type(exc).__name__,
            err=str(exc)[:300],
        )
        raise HTTPException(status_code=502, detail="Failed to query demo runs.") from exc

    raw_rows = res.data or []

    # ---- Subjects batch lookup (roof badge + recipient address) --------
    # Pull the subject row for every lead in the page in one ``in`` query.
    # Soft-fail: if the subjects table is unreachable, the page still
    # loads — the rows just lose their roof badge / recipient column.
    lead_ids_for_subjects = [r["lead_id"] for r in raw_rows if r.get("lead_id")]
    subjects_by_lead: dict[str, dict[str, Any]] = {}
    if lead_ids_for_subjects:
        try:
            leads_res = (
                sb.table("leads")
                .select(
                    "id, subject_id, "
                    "subjects(decision_maker_email, sede_operativa_source, "
                    "sede_operativa_confidence)"
                )
                .in_("id", lead_ids_for_subjects)
                .execute()
            )
            for lr in leads_res.data or []:
                lid = lr.get("id")
                subj = lr.get("subjects") or {}
                if isinstance(subj, list):
                    subj = subj[0] if subj else {}
                if lid:
                    subjects_by_lead[lid] = subj or {}
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            log.warning(
                "admin.demo_runs_subjects_lookup_failed",
                err_type=type(exc).__name__,
                err=str(exc)[:160],
            )

    # ---- Email status batch lookup -------------------------------------
    # outreach_sends has multiple rows per lead in production (re-sends,
    # follow-ups). For demo runs we want the FIRST send (the one created by
    # the demo pipeline). Doing this as one batched ``in`` query keeps the
    # endpoint at O(2) round-trips regardless of page size.
    lead_ids = [r["lead_id"] for r in raw_rows if r.get("lead_id")]
    sends_by_lead: dict[str, dict[str, Any]] = {}
    if lead_ids:
        try:
            # Note: outreach_sends does not store recipient_email — the
            # actual recipient is on leads.decision_maker_email (already
            # joined above). We only project the send-level metadata here.
            sends_res = (
                sb.table("outreach_sends")
                .select(
                    "lead_id, status, failure_reason, email_message_id, "
                    "sent_at, created_at"
                )
                .in_("lead_id", lead_ids)
                .order("created_at", desc=False)  # earliest first → demo send wins
                .execute()
            )
            for s in sends_res.data or []:
                lid = s.get("lead_id")
                if lid and lid not in sends_by_lead:
                    sends_by_lead[lid] = s
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            log.warning("admin.demo_runs_email_status_lookup_failed", err=str(exc))

    rows: list[DemoRunRow] = []
    for r in raw_rows:
        tenant_data = r.get("tenants") or {}
        # Subject is now sourced from the separate batch lookup above
        # (subjects_by_lead) instead of an embedded-resource select.
        subject_data = subjects_by_lead.get(r.get("lead_id") or "") or {}

        send_row = sends_by_lead.get(r.get("lead_id") or "") or {}

        rows.append(
            DemoRunRow(
                id=r["id"],
                tenant_id=r["tenant_id"],
                tenant_name=tenant_data.get("business_name") if isinstance(tenant_data, dict) else None,
                lead_id=r.get("lead_id"),
                status=r["status"],
                failed_step=r.get("failed_step"),
                error_message=r.get("error_message"),
                notes=r.get("notes"),
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                email_status=send_row.get("status"),
                email_status_detail=send_row.get("failure_reason"),
                email_message_id=send_row.get("email_message_id"),
                email_sent_at=send_row.get("sent_at"),
                email_recipient=(subject_data or {}).get("decision_maker_email"),
                roof_source=(subject_data or {}).get("sede_operativa_source"),
                roof_confidence=(subject_data or {}).get("sede_operativa_confidence"),
            )
        )

    log.info("admin.demo_runs_listed", count=len(rows), total=total, super_admin=ctx.sub)
    return DemoRunsResponse(runs=rows, total=total)
