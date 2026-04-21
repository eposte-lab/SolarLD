"""Modular wizard HTTP surface — `/v1/modules/*`.

Surface area:

  GET  /v1/modules                  → list all 5 modules (missing ones
                                      synthesised with defaults)
  GET  /v1/modules/{key}            → one module (same default policy)
  PUT  /v1/modules/{key}            → upsert config and/or toggle active
  POST /v1/modules/{key}/preview    → dry-run: validate the payload +
                                      return cost/count estimates
                                      without persisting

Each route is tenant-scoped via `require_tenant(ctx)` and delegates
to `services.tenant_module_service`. Validation happens there against
the Pydantic schemas; this module is a thin HTTP adapter.

The **preview** endpoint is specific to the Sorgente module today —
it estimates how many Atoka records would match the proposed
criteria, letting the installer iterate on geography/ATECO before
committing a scan. Other modules fall back to an echo response (the
validated config).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..services.tenant_module_service import (
    MODULE_KEYS,
    ModuleKey,
    TenantModule,
    all_completed,
    get_module,
    list_modules,
    schema_for,
    upsert_module,
    validate_config,
)

router = APIRouter()
log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class ModuleUpsertIn(BaseModel):
    """Body accepted by `PUT /v1/modules/{key}`.

    Both `config` and `active` are optional — the caller can flip
    active without re-sending the whole config (e.g. "disable
    outreach for this tenant while debugging a creative bug").
    """

    model_config = ConfigDict(extra="forbid")

    config: dict[str, Any] | None = None
    active: bool | None = None


class ModulePreviewIn(BaseModel):
    """Body for `POST /v1/modules/{key}/preview` — the wizard
    uses this to show "your criteria match ~N prospects" before
    save. Shape identical to upsert minus `active`."""

    model_config = ConfigDict(extra="forbid")

    config: dict[str, Any] = Field(default_factory=dict)


class ModuleListOut(BaseModel):
    """Envelope for `GET /v1/modules` — includes a convenience
    `wizard_complete` flag so the dashboard can route to
    `/onboarding` vs `/dashboard` without a second query."""

    modules: list[TenantModule]
    wizard_complete: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_key(key: str) -> ModuleKey:
    """Narrow a URL path component to `ModuleKey` or 404."""
    if key not in MODULE_KEYS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown module_key '{key}'. Valid: {list(MODULE_KEYS)}",
        )
    return key  # type: ignore[return-value]


def _raise_validation(exc: ValidationError) -> None:
    """Convert pydantic ValidationError → 422 with a flat error list."""
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=exc.errors(include_url=False),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=ModuleListOut)
async def read_all_modules(ctx: CurrentUser) -> ModuleListOut:
    """Return all 5 modules for the caller's tenant.

    Missing rows are synthesised with schema defaults — the client
    always sees 5 entries so UI rendering is uniform across
    freshly-created and established tenants.
    """
    tenant_id = require_tenant(ctx)
    mods = await list_modules(tenant_id)
    done = await all_completed(tenant_id)
    return ModuleListOut(modules=mods, wizard_complete=done)


@router.get("/schemas")
async def read_schemas(ctx: CurrentUser) -> dict[str, Any]:
    """Return the JSON Schema for every module config body.

    The frontend uses these to drive dynamic form generation (react-jsonschema-form
    or the hand-rolled module forms in `/onboarding/_modules/*`). Returning
    them as a single endpoint means the dashboard always stays in sync
    with the backend — no duplicated TypeScript types to maintain manually
    for the config shape itself (only the wrapper types).

    Gated by auth so we don't leak the internals to unauthenticated
    callers, but the content itself isn't tenant-specific.
    """
    require_tenant(ctx)
    return {key: schema_for(key).model_json_schema() for key in MODULE_KEYS}


@router.get("/{key}", response_model=TenantModule)
async def read_one_module(ctx: CurrentUser, key: str) -> TenantModule:
    """Return one module's config.

    Returns 200 with default config even if no row exists yet — a
    missing module is a valid state, not an error.
    """
    tenant_id = require_tenant(ctx)
    module_key = _coerce_key(key)
    return await get_module(tenant_id, module_key)


@router.put("/{key}", response_model=TenantModule)
async def upsert_one_module(
    ctx: CurrentUser, key: str, payload: ModuleUpsertIn
) -> TenantModule:
    """Upsert one module's config and/or `active` flag.

    Validation errors bubble up as 422 with the pydantic error list
    so the client can highlight the offending field. Idempotent: two
    identical PUTs produce the same final state (and the trigger
    skips the version bump on no-op edits).
    """
    tenant_id = require_tenant(ctx)
    module_key = _coerce_key(key)

    if payload.config is None and payload.active is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one of `config` or `active`.",
        )

    try:
        return await upsert_module(
            tenant_id,
            module_key,
            config=payload.config,
            active=payload.active,
        )
    except ValidationError as exc:
        _raise_validation(exc)
        raise  # unreachable — _raise_validation raises; keeps type-checker happy


@router.post("/{key}/preview")
async def preview_module(
    ctx: CurrentUser, key: str, payload: ModulePreviewIn
) -> dict[str, Any]:
    """Dry-run: validate the proposed config, and for Sorgente also
    estimate the resulting Atoka match count.

    Response shape (stable across modules):
        {
            "valid": bool,
            "normalised": <config dict after defaults/dedup>,
            "estimate": {...module-specific stats...}
        }

    Scope intentionally minimal for now — Phase 2 ships this as a
    placeholder that the Sorgente form can call to get back a
    normalised config echo. Real Atoka-count estimation lands in
    Phase 3 when we have a live count endpoint to proxy.
    """
    require_tenant(ctx)
    module_key = _coerce_key(key)

    try:
        normalised = validate_config(module_key, payload.config)
    except ValidationError as exc:
        _raise_validation(exc)
        raise  # unreachable

    estimate: dict[str, Any] = {}
    if module_key == "sorgente":
        # Placeholder heuristic until we wire a real Atoka `/count`
        # endpoint. Gives the installer directional feedback:
        # more ATECO codes + more provinces → bigger estimate.
        ateco = normalised.get("ateco_codes") or []
        province = normalised.get("province") or []
        regioni = normalised.get("regioni") or []
        # Rough: 50 companies per ATECO × province, 500 per regione.
        estimate["atoka_rough_count"] = (
            len(ateco) * (max(1, len(province)) * 50 + len(regioni) * 500)
        )
        estimate["note"] = (
            "Rough placeholder estimate. Live Atoka count endpoint "
            "lands in Phase 3."
        )
    elif module_key == "economico":
        # Project budget into estimated L1/L2/L4 candidate counts.
        budget = float(normalised.get("budget_scan_eur") or 0)
        estimate["max_candidates_l1"] = int(budget / 0.01)  # Atoka €0.01/record
        estimate["max_candidates_solar"] = int(budget / 0.03)  # Solar €0.03/call

    return {"valid": True, "normalised": normalised, "estimate": estimate}
