"""Tenant operational-config endpoints (Sprint 9, Fase C).

Surface area:

  GET  /v1/tenant-config              → current config (typed)
  GET  /v1/tenant-config/options      → ATECO wizard dropdown data
  POST /v1/tenant-config              → wizard submit / update

All routes are tenant-scoped via `require_tenant(ctx)` and delegate to
`services.tenant_config_service` — the route module is a thin
validation + HTTP wrapper, no business logic leaks here.

Validation strategy:
  - Pydantic models enforce shape + types (scan_mode literal, segment
    whitelist, non-negative budgets, bounded shading/exposure).
  - The DAO re-validates referential integrity (ateco_codes must exist
    in `ateco_google_types`) so a client forging codes still fails
    gracefully with an empty whitelist — never persists bad data.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..services.tenant_config_service import (
    TenantConfig,
    WizardPayload,
    get_for_tenant,
    list_ateco_options,
    upsert_from_wizard,
)

router = APIRouter()
log = get_logger(__name__)


# ---------------------------------------------------------------------------
# tenants.settings — whitelist of integration keys the wizard may persist.
# Declared at module scope so the contract is grep-able from tests and
# future code (e.g. a Settings UI that reads the same list).
# ---------------------------------------------------------------------------

_INTEGRATION_SETTINGS_KEYS: tuple[str, ...] = (
    "neverbounce_api_key",
    "dialog360_token",
    "dialog360_business_number",
    "resend_webhook_secret",
)


def _merge_integrations_into_tenant_settings(
    tenant_id: UUID | str, integrations: "IntegrationsIn"
) -> None:
    """Merge only non-empty integration fields into `tenants.settings`.

    - Empty strings are treated as "user skipped this field" — we
      do NOT overwrite any existing value.
    - Other fields in `settings` (branding, feature flags, etc.) are
      preserved: we read, merge at the Python level, write back.

    This intentionally lives here rather than in `tenant_config_service`
    because the service layer is strictly about `tenant_configs`; the
    `tenants.settings` JSONB is governed by the tenants route family.
    """
    updates: dict[str, str] = {
        key: value
        for key, value in integrations.model_dump().items()
        if key in _INTEGRATION_SETTINGS_KEYS and value
    }
    if not updates:
        return

    sb = get_service_client()
    tid = str(tenant_id)

    current = (
        sb.table("tenants").select("settings").eq("id", tid).maybe_single().execute()
    )
    existing_settings = (getattr(current, "data", None) or {}).get("settings") or {}
    merged = {**existing_settings, **updates}

    sb.table("tenants").update({"settings": merged}).eq("id", tid).execute()

    log.info(
        "tenant.integrations.upserted",
        tenant_id=tid,
        keys=sorted(updates.keys()),
    )

# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class TechnicalFiltersOut(BaseModel):
    """Response-side projection of `TechnicalFilters`."""

    min_area_sqm: float
    min_kwp: float
    max_shading: float
    min_exposure_score: float


class TenantConfigOut(BaseModel):
    """Response shape for GET /v1/tenant-config.

    Mirrors the dashboard's `TenantConfigRow` type so the TS and Python
    contracts stay aligned (any new field here must be added there too).
    """

    tenant_id: UUID
    scan_mode: Literal["b2b_precision", "opportunistic", "volume"]
    target_segments: list[str]
    place_type_whitelist: list[str]
    place_type_priority: dict[str, int]
    ateco_whitelist: list[str]
    ateco_blacklist: list[str]
    ateco_priority: dict[str, int]
    min_employees: int | None
    max_employees: int | None
    min_revenue_eur: int | None
    max_revenue_eur: int | None
    technical_b2b: TechnicalFiltersOut
    technical_b2c: TechnicalFiltersOut
    scoring_threshold: int
    scoring_weights: dict[str, dict[str, int]]
    monthly_scan_budget_eur: float
    monthly_outreach_budget_eur: float
    scan_priority_zones: list[str]
    scan_grid_density_m: int
    atoka_enabled: bool
    atoka_monthly_cap_eur: float
    wizard_completed_at: str | None
    wizard_pending: bool


def _to_out(cfg: TenantConfig) -> TenantConfigOut:
    """Adapt the domain dataclass to the HTTP response model."""
    return TenantConfigOut(
        tenant_id=cfg.tenant_id,
        scan_mode=cfg.scan_mode,
        target_segments=list(cfg.target_segments),
        place_type_whitelist=list(cfg.place_type_whitelist),
        place_type_priority=cfg.place_type_priority,
        ateco_whitelist=list(cfg.ateco_whitelist),
        ateco_blacklist=list(cfg.ateco_blacklist),
        ateco_priority=cfg.ateco_priority,
        min_employees=cfg.min_employees,
        max_employees=cfg.max_employees,
        min_revenue_eur=cfg.min_revenue_eur,
        max_revenue_eur=cfg.max_revenue_eur,
        technical_b2b=TechnicalFiltersOut(
            min_area_sqm=cfg.technical_b2b.min_area_sqm,
            min_kwp=cfg.technical_b2b.min_kwp,
            max_shading=cfg.technical_b2b.max_shading,
            min_exposure_score=cfg.technical_b2b.min_exposure_score,
        ),
        technical_b2c=TechnicalFiltersOut(
            min_area_sqm=cfg.technical_b2c.min_area_sqm,
            min_kwp=cfg.technical_b2c.min_kwp,
            max_shading=cfg.technical_b2c.max_shading,
            min_exposure_score=cfg.technical_b2c.min_exposure_score,
        ),
        scoring_threshold=cfg.scoring_threshold,
        scoring_weights=cfg.scoring_weights,
        monthly_scan_budget_eur=cfg.monthly_scan_budget_eur,
        monthly_outreach_budget_eur=cfg.monthly_outreach_budget_eur,
        scan_priority_zones=list(cfg.scan_priority_zones),
        scan_grid_density_m=cfg.scan_grid_density_m,
        atoka_enabled=cfg.atoka_enabled,
        atoka_monthly_cap_eur=cfg.atoka_monthly_cap_eur,
        wizard_completed_at=cfg.wizard_completed_at.isoformat()
        if cfg.wizard_completed_at is not None
        else None,
        wizard_pending=cfg.wizard_pending,
    )


class IntegrationsIn(BaseModel):
    """Optional Step 6 payload — provider keys the wizard can't infer.

    All fields are optional; empty strings are filtered by the route
    handler before the `tenants.settings` merge, so skipping the step
    (or clearing a field) does NOT wipe previously-stored values.
    """

    neverbounce_api_key: str = ""
    dialog360_token: str = ""
    dialog360_business_number: str = ""
    resend_webhook_secret: str = ""


class WizardIn(BaseModel):
    """Incoming wizard submission.

    Pydantic handles coercion + bounds; any missing optional field
    falls back to a schema-level default that matches
    `WizardPayload.__init__` defaults.
    """

    scan_mode: Literal["b2b_precision", "opportunistic", "volume"]
    target_segments: list[Literal["b2b", "b2c"]] = Field(..., min_length=1)
    ateco_codes: list[str] = Field(default_factory=list)
    min_kwp_b2b: float | None = Field(None, ge=0, le=10_000)
    min_kwp_b2c: float | None = Field(None, ge=0, le=100)
    max_shading: float = Field(0.5, ge=0, le=1)
    min_exposure_score: float = Field(0.6, ge=0, le=1)
    scan_priority_zones: list[str] = Field(default_factory=lambda: ["capoluoghi"])
    monthly_scan_budget_eur: float = Field(1500.0, ge=0, le=100_000)
    monthly_outreach_budget_eur: float = Field(2000.0, ge=0, le=100_000)
    scoring_threshold: int = Field(60, ge=0, le=100)
    integrations: IntegrationsIn = Field(default_factory=IntegrationsIn)

    @field_validator("ateco_codes")
    @classmethod
    def _dedupe_ateco(cls, v: list[str]) -> list[str]:
        """Dedupe while preserving order."""
        seen: set[str] = set()
        out: list[str] = []
        for code in v:
            if code not in seen:
                seen.add(code)
                out.append(code)
        return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=TenantConfigOut)
async def read_tenant_config(ctx: CurrentUser) -> TenantConfigOut:
    """Return the caller's tenant operational config.

    Always returns 200 — if the row is missing, the DAO provides a
    safe default with `wizard_pending=True` so the client can route
    the user to `/onboarding`.
    """
    tenant_id = require_tenant(ctx)
    cfg = await get_for_tenant(tenant_id)
    return _to_out(cfg)


@router.get("/options")
async def read_ateco_options(ctx: CurrentUser) -> list[dict[str, Any]]:
    """Return the wizard ATECO dropdown grouped by `wizard_group`.

    The tenant association doesn't affect the data (it's a global
    reference table) but we still gate it behind auth so the catalog
    doesn't leak publicly.
    """
    require_tenant(ctx)
    return await list_ateco_options()


@router.post("", response_model=TenantConfigOut, status_code=status.HTTP_200_OK)
async def submit_wizard(ctx: CurrentUser, payload: WizardIn) -> TenantConfigOut:
    """Persist a wizard submission and return the fresh config.

    Idempotent on (tenant_id) — re-submitting overwrites the previous
    wizard output. `wizard_completed_at` is stamped server-side.
    """
    tenant_id = require_tenant(ctx)

    # Guard: b2b_precision requires at least one ATECO code so the
    # Places whitelist is not just ["establishment"] (which would
    # defeat the precision scan).
    if payload.scan_mode == "b2b_precision" and not payload.ateco_codes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="b2b_precision scan_mode requires at least one ATECO code",
        )

    wizard = WizardPayload(
        scan_mode=payload.scan_mode,
        target_segments=list(payload.target_segments),
        ateco_codes=list(payload.ateco_codes),
        min_kwp_b2b=payload.min_kwp_b2b,
        min_kwp_b2c=payload.min_kwp_b2c,
        max_shading=payload.max_shading,
        min_exposure_score=payload.min_exposure_score,
        scan_priority_zones=list(payload.scan_priority_zones),
        monthly_scan_budget_eur=payload.monthly_scan_budget_eur,
        monthly_outreach_budget_eur=payload.monthly_outreach_budget_eur,
        scoring_threshold=payload.scoring_threshold,
    )
    cfg = await upsert_from_wizard(tenant_id, wizard)

    # Step 6 — optional provider integrations. Runs AFTER the tenant
    # config upsert so that a failure here leaves the main wizard state
    # intact (the installer can retry from /settings without losing
    # their earlier selections).
    _merge_integrations_into_tenant_settings(tenant_id, payload.integrations)

    return _to_out(cfg)
