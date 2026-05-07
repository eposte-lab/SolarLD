"""Prospector — "Trova aziende" HTTP surface (v3 Google Places).

Standalone search & list-management API for the dashboard /scoperta page.
Lives next to (not inside) the Hunter L1-L4 funnel: this is the
operator-driven manual workflow, the funnel is the automated pipeline.

v3 (Sprint maggio 2026): la ricerca è stata migrata da Atoka a Google
Places. I filtri ATECO/employees/revenue (Atoka-only) sono stati
rimossi; al loro posto: settore (wizard_group), comune o provincia,
raggio km, keyword. Le liste salvate possono essere convalidate
on-demand (esegue L2-L5 funnel) e poi avviare outreach on-demand.

Endpoints
---------
GET    /v1/prospector/presets                       Settori disponibili
POST   /v1/prospector/search                        Live Places search (no DB)
POST   /v1/prospector/lists                         Persist a saved list
GET    /v1/prospector/lists                         Index of saved lists
GET    /v1/prospector/lists/{id}                    Load list with items
DELETE /v1/prospector/lists/{id}                    Hard-delete cascade
POST   /v1/prospector/lists/{id}/validate           Enqueue convalida v3
GET    /v1/prospector/lists/{id}/validate/status    Validation progress
POST   /v1/prospector/lists/{id}/launch-outreach    Enqueue outreach
GET    /v1/prospector/lists/{id}/outreach/status    Outreach progress
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.queue import enqueue
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..services import prospector_service
from ..services.places_prospector_service import search_places
from ..services.places_to_sector import _SECTOR_TO_INCLUDED_TYPES

router = APIRouter()
log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class SearchInput(BaseModel):
    """Body of POST /v1/prospector/search (v3 Places-based).

    Settore is required — without an `includedPrimaryTypes` filter the
    Places API returns far too broad a result set for the operator to
    work with. comune OR province_code is required for geo anchor.
    """

    sector: str = Field(min_length=1, max_length=50)
    province_code: str | None = Field(default=None, min_length=2, max_length=2)
    comune: str | None = Field(default=None, max_length=120)
    radius_km: int = Field(default=30, ge=5, le=50)
    keyword: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=60, ge=1, le=200)


class CreateListInput(BaseModel):
    """Body of POST /v1/prospector/lists (v3 Places-based)."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    search_filter: dict[str, Any] = Field(default_factory=dict)
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=5000)
    # Default 'solar_rooftop' keeps the existing /scoperta flow unchanged.
    # 'generic_outreach' bypasses the L4 Solar gate so non-rooftop
    # campaigns (amministratori condominio, dental clinics, …) become
    # ready-to-send leads after L2 scraping alone.
    campaign_type: str = Field(default="solar_rooftop")


# ---------------------------------------------------------------------------
# Presets — sectors available
# ---------------------------------------------------------------------------


@router.get("/presets")
async def list_presets(ctx: CurrentUser) -> dict[str, Any]:
    """Return the wizard_group sectors that have a Places primary-type
    mapping. The dashboard renders these as the "Settore" dropdown.

    The Italian labels live on the dashboard side
    (`lib/sector-labels.ts`) — here we just expose the slugs.
    """
    require_tenant(ctx)
    return {"sectors": sorted(_SECTOR_TO_INCLUDED_TYPES.keys())}


# ---------------------------------------------------------------------------
# Search — live Google Places call (no DB write)
# ---------------------------------------------------------------------------


@router.post("/search")
async def prospector_search(body: SearchInput, ctx: CurrentUser) -> dict[str, Any]:
    """Live Places discovery search — no DB write.

    Returns ``items`` shaped for direct table render in the dashboard:
    a list of flat dicts mirroring ``ProspectorPlace``.
    """
    require_tenant(ctx)

    if not body.province_code and not body.comune:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="province_or_comune_required",
        )

    if body.sector not in _SECTOR_TO_INCLUDED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"sector_not_supported:{body.sector}",
        )

    places = await search_places(
        sector=body.sector,
        province_code=body.province_code,
        comune=body.comune,
        radius_km=body.radius_km,
        keyword=body.keyword,
        limit=body.limit,
    )

    return {
        "items": [asdict(p) for p in places],
        "count": len(places),
    }


# ---------------------------------------------------------------------------
# Lists — CRUD
# ---------------------------------------------------------------------------


@router.post("/lists", status_code=status.HTTP_201_CREATED)
async def create_list(body: CreateListInput, ctx: CurrentUser) -> dict[str, Any]:
    """Persist a Places-based list + its items as a durable artefact.

    Items are stored with ``validation_status='pending'`` and can be
    promoted via POST /v1/prospector/lists/{id}/validate.
    """
    tenant_id = require_tenant(ctx)
    if body.campaign_type not in ("solar_rooftop", "generic_outreach"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="campaign_type must be 'solar_rooftop' or 'generic_outreach'",
        )
    try:
        row = prospector_service.create_places_list(
            tenant_id=tenant_id,
            name=body.name,
            description=body.description,
            search_filter=body.search_filter,
            items=body.items,
            created_by=ctx.user_id,
            campaign_type=body.campaign_type,
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


class PatchListInput(BaseModel):
    """Body of PATCH /v1/prospector/lists/{id} — only writable fields."""

    email_template_id: str | None = Field(default=..., description="UUID or null to unlink")


@router.patch("/lists/{list_id}", status_code=status.HTTP_200_OK)
async def patch_list(list_id: str, body: PatchListInput, ctx: CurrentUser) -> dict[str, Any]:
    """Update mutable fields on a prospect_list (currently: email_template_id)."""
    tenant_id = require_tenant(ctx)
    if not _list_belongs_to_tenant(list_id, tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="list_not_found")

    sb = get_service_client()
    res = (
        sb.table("prospect_lists")
        .update({"email_template_id": body.email_template_id})
        .eq("id", list_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="patch_failed",
        )
    return res.data[0]


# ---------------------------------------------------------------------------
# Validation v3 — enqueue + status
# ---------------------------------------------------------------------------


def _list_belongs_to_tenant(list_id: str, tenant_id: str) -> bool:
    sb = get_service_client()
    res = (
        sb.table("prospect_lists")
        .select("id")
        .eq("tenant_id", tenant_id)
        .eq("id", list_id)
        .limit(1)
        .execute()
    )
    return bool(res.data)


@router.post("/lists/{list_id}/validate", status_code=status.HTTP_202_ACCEPTED)
async def validate_list(list_id: str, ctx: CurrentUser) -> dict[str, Any]:
    """Enqueue the on-demand convalida task for a list.

    The task fans out L2-L5 (scraping, quality, Solar API, scoring) per
    item and updates `validation_status`. Idempotent: a second click
    while the first run is in flight is collapsed at the ARQ level.
    """
    tenant_id = require_tenant(ctx)
    if not _list_belongs_to_tenant(list_id, tenant_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="list_not_found",
        )

    job_id = f"validate_prospect_list:{tenant_id}:{list_id}:{int(time.time())}"
    job = await enqueue(
        "validate_prospect_list_task",
        {"tenant_id": tenant_id, "list_id": list_id},
        job_id=job_id,
    )
    log.info(
        "prospector.validate_enqueued",
        tenant_id=tenant_id,
        list_id=list_id,
        job_id=job_id,
    )
    return {"queued": True, **job}


@router.get("/lists/{list_id}/validate/status")
async def validate_status(list_id: str, ctx: CurrentUser) -> dict[str, Any]:
    """Aggregate progress: per-status counts + lifecycle timestamps."""
    tenant_id = require_tenant(ctx)
    if not _list_belongs_to_tenant(list_id, tenant_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="list_not_found",
        )

    sb = get_service_client()
    list_res = (
        sb.table("prospect_lists")
        .select("validation_started_at, validation_completed_at, item_count")
        .eq("id", list_id)
        .limit(1)
        .execute()
    )
    list_row = (list_res.data or [{}])[0]

    items = (
        sb.table("prospect_list_items").select("validation_status").eq("list_id", list_id).execute()
    )

    counts: dict[str, int] = {}
    for row in items.data or []:
        s = row.get("validation_status") or "pending"
        counts[s] = counts.get(s, 0) + 1

    return {
        "list_id": list_id,
        "started_at": list_row.get("validation_started_at"),
        "completed_at": list_row.get("validation_completed_at"),
        "item_count": list_row.get("item_count") or 0,
        "by_status": counts,
    }


# ---------------------------------------------------------------------------
# Outreach launch v3 — enqueue + status
# ---------------------------------------------------------------------------


@router.post(
    "/lists/{list_id}/launch-outreach",
    status_code=status.HTTP_202_ACCEPTED,
)
async def launch_outreach(list_id: str, ctx: CurrentUser) -> dict[str, Any]:
    """Promote `validation_status='accepted'` items to subjects+leads
    and queue outreach for each. The daily cap is enforced per-task at
    the OutreachAgent gate — over-cap items are deferred to next day.
    """
    tenant_id = require_tenant(ctx)
    if not _list_belongs_to_tenant(list_id, tenant_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="list_not_found",
        )

    job_id = f"launch_outreach_for_list:{tenant_id}:{list_id}:{int(time.time())}"
    job = await enqueue(
        "launch_outreach_for_list_task",
        {"tenant_id": tenant_id, "list_id": list_id},
        job_id=job_id,
    )
    log.info(
        "prospector.outreach_enqueued",
        tenant_id=tenant_id,
        list_id=list_id,
        job_id=job_id,
    )
    return {"queued": True, **job}


@router.get("/lists/{list_id}/outreach/status")
async def outreach_status(list_id: str, ctx: CurrentUser) -> dict[str, Any]:
    """Outreach launch progress: counts by lead-pipeline stage."""
    tenant_id = require_tenant(ctx)
    if not _list_belongs_to_tenant(list_id, tenant_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="list_not_found",
        )

    sb = get_service_client()
    list_res = (
        sb.table("prospect_lists")
        .select("outreach_started_at, outreach_completed_at")
        .eq("id", list_id)
        .limit(1)
        .execute()
    )
    list_row = (list_res.data or [{}])[0]

    # Count of accepted items (the universe to launch).
    accepted = (
        sb.table("prospect_list_items")
        .select("scan_candidate_id", count="exact", head=True)
        .eq("list_id", list_id)
        .eq("validation_status", "accepted")
        .execute()
    )

    # We can't easily count "leads created from this list" without a
    # dedicated fk. Rough proxy: count distinct scan_candidate_id rows
    # whose subject has been promoted (subjects_count_for_list helper —
    # adopted on demand).
    return {
        "list_id": list_id,
        "started_at": list_row.get("outreach_started_at"),
        "completed_at": list_row.get("outreach_completed_at"),
        "accepted_count": accepted.count or 0,
    }
