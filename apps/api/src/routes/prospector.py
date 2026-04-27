"""Prospector — "Trova aziende" HTTP surface.

Standalone search & list-management API for the dashboard /scoperta page.
Lives next to (not inside) the Hunter L1-L4 funnel: this is the
operator-driven manual workflow, the funnel is the automated pipeline.

Endpoints
---------
GET    /v1/prospector/presets              ATECO preset chips
POST   /v1/prospector/search               Live Atoka search (no DB write)
GET    /v1/prospector/cost-estimate        Pre-flight cost estimate
POST   /v1/prospector/lists                Persist a saved list with items
GET    /v1/prospector/lists                Index of saved lists
GET    /v1/prospector/lists/{id}           Load a list with paginated items
DELETE /v1/prospector/lists/{id}           Hard-delete a list (cascade items)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..services import prospector_service

router = APIRouter()
log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class SearchInput(BaseModel):
    """Body of POST /v1/prospector/search.

    All filters are optional except `ateco_codes` — Atoka requires at
    least one code to scope the search; otherwise the cost / latency
    of an "all of Italy" query is unbounded.
    """

    ateco_codes: list[str] = Field(default_factory=list, max_length=50)
    province_code: str | None = Field(default=None, max_length=2)
    region_code: str | None = Field(default=None, max_length=10)
    employees_min: int | None = Field(default=None, ge=0)
    employees_max: int | None = Field(default=None, ge=0)
    revenue_min_eur: int | None = Field(default=None, ge=0)
    revenue_max_eur: int | None = Field(default=None, ge=0)
    keyword: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
    # Optional preset id — surfaced in the saved list payload so the UI
    # can render the chip ("Amministratori condominio", ecc.).
    preset_code: str | None = Field(default=None, max_length=80)


class CreateListInput(BaseModel):
    """Body of POST /v1/prospector/lists."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    # Echo of the search filter that produced the items — stored as
    # JSONB so the operator can rebuild / refresh the list later.
    search_filter: dict[str, Any] = Field(default_factory=dict)
    preset_code: str | None = Field(default=None, max_length=80)
    # Items are flat dicts shaped like prospector_service.search() output.
    # We persist them verbatim (snapshot) — the durable artefact survives
    # Atoka catalog churn.
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=5000)


# ---------------------------------------------------------------------------
# Presets — static catalogue
# ---------------------------------------------------------------------------


@router.get("/presets")
async def list_presets(ctx: CurrentUser) -> dict[str, Any]:
    """ATECO presets surfaced as one-click chips on /scoperta.

    Returns the dict from `prospector_service.ATECO_PRESETS` keyed by
    preset_code (e.g. ``amministratori_condominio``). Static for now;
    when we move to per-tenant custom presets this endpoint will read
    from a `tenant_prospector_presets` table.
    """
    require_tenant(ctx)  # auth gate even though payload is static
    return {"presets": prospector_service.ATECO_PRESETS}


# ---------------------------------------------------------------------------
# Search — live Atoka call
# ---------------------------------------------------------------------------


@router.post("/search")
async def prospector_search(body: SearchInput, ctx: CurrentUser) -> dict[str, Any]:
    """Live Atoka discovery search — no DB write.

    The result payload is shaped for direct table render in the
    dashboard: ``items`` is a list of flat dicts with snake_case
    column-friendly keys (vat_number, legal_name, ateco_code, ...).
    """
    require_tenant(ctx)

    if not body.ateco_codes:
        # Mirror the service-level early-out so the UI gets a clean
        # 400 instead of a generic 500 when the form is submitted
        # without a preset / custom code selection.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ateco_codes_required",
        )

    return await prospector_service.search(
        ateco_codes=body.ateco_codes,
        province_code=body.province_code,
        region_code=body.region_code,
        employees_min=body.employees_min,
        employees_max=body.employees_max,
        revenue_min_eur=body.revenue_min_eur,
        revenue_max_eur=body.revenue_max_eur,
        keyword=body.keyword,
        limit=body.limit,
        offset=body.offset,
    )


@router.get("/cost-estimate")
async def cost_estimate(
    ctx: CurrentUser,
    record_count: int = Query(ge=0, le=10_000),
) -> dict[str, Any]:
    """Pre-flight cost estimate (€) for a planned search.

    Used by the UI to show the running tab BEFORE the operator pulls
    the trigger on a 1k-record discovery. Cheap, no Atoka call.
    """
    require_tenant(ctx)
    return {
        "record_count": record_count,
        "estimated_cost_eur": prospector_service.estimate_cost(record_count),
    }


# ---------------------------------------------------------------------------
# Lists — CRUD
# ---------------------------------------------------------------------------


@router.post("/lists", status_code=status.HTTP_201_CREATED)
async def create_list(body: CreateListInput, ctx: CurrentUser) -> dict[str, Any]:
    """Persist a list + its items as a durable artefact."""
    tenant_id = require_tenant(ctx)
    try:
        row = prospector_service.create_list(
            tenant_id=tenant_id,
            name=body.name,
            description=body.description,
            search_filter=body.search_filter,
            items=body.items,
            preset_code=body.preset_code,
            created_by=ctx.user_id,
        )
    except RuntimeError as exc:
        log.error("prospector.create_list_failed", tenant_id=tenant_id, err=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="create_list_failed",
        ) from exc
    return row


@router.get("/lists")
async def list_lists(
    ctx: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Index of saved lists for the current tenant (most recent first)."""
    tenant_id = require_tenant(ctx)
    return prospector_service.list_lists(
        tenant_id=tenant_id,
        page=page,
        page_size=page_size,
    )


@router.get("/lists/{list_id}")
async def get_list(
    list_id: str,
    ctx: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """Load a list with paginated items."""
    tenant_id = require_tenant(ctx)
    res = prospector_service.get_list(
        tenant_id=tenant_id,
        list_id=list_id,
        page=page,
        page_size=page_size,
    )
    if res is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="list_not_found",
        )
    return res


@router.delete("/lists/{list_id}", status_code=status.HTTP_200_OK)
async def delete_list(list_id: str, ctx: CurrentUser) -> dict[str, Any]:
    """Hard-delete a list and cascade its items."""
    tenant_id = require_tenant(ctx)
    deleted = prospector_service.delete_list(
        tenant_id=tenant_id,
        list_id=list_id,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="list_not_found",
        )
    return {"deleted": True, "id": list_id}
