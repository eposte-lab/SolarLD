"""Sector news catalogue API (Sprint 10).

Tenant-scoped CRUD over the ``sector_news`` table. Operators curate
sector-relevant signals here; the engagement-based follow-up engine
(``followup_engaged.j2`` / ``followup_lukewarm.j2``) reads them at
render time to compose copy that quotes a sector fact instead of
mentioning tracked behaviour.

Routes
------
GET    /v1/sector-news/                 List visible rows (own + global)
POST   /v1/sector-news/                 Create new tenant-scoped row
PATCH  /v1/sector-news/{news_id}        Update one tenant-owned row
DELETE /v1/sector-news/{news_id}        Soft-archive (status='archived')

Global rows (tenant_id IS NULL) are read-only — operators clone-and-
override by creating their own row with the same ATECO 2-digit which
takes precedence in lookup.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..services import sector_news_service

log = get_logger(__name__)
router = APIRouter(prefix="/sector-news", tags=["sector-news"])


class SectorNewsCreate(BaseModel):
    ateco_2digit: str = Field(min_length=2, max_length=2, pattern=r"^\d{2}$")
    headline: str = Field(min_length=10, max_length=140)
    body: str = Field(min_length=20, max_length=600)
    source_url: str | None = None


class SectorNewsUpdate(BaseModel):
    ateco_2digit: str | None = Field(
        default=None, min_length=2, max_length=2, pattern=r"^\d{2}$"
    )
    headline: str | None = Field(default=None, min_length=10, max_length=140)
    body: str | None = Field(default=None, min_length=20, max_length=600)
    source_url: str | None = None
    status: str | None = Field(default=None, pattern=r"^(active|archived)$")


@router.get("/")
async def list_sector_news(user: CurrentUser) -> dict[str, Any]:
    """Return all visible sector-news rows (this tenant + global seeds)."""
    tenant_id = require_tenant(user)
    sb = get_service_client()
    rows = await sector_news_service.list_for_tenant(sb, tenant_id)
    return {"rows": rows, "total": len(rows)}


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_sector_news(
    payload: SectorNewsCreate, user: CurrentUser
) -> dict[str, Any]:
    """Insert a new tenant-scoped sector-news row."""
    tenant_id = require_tenant(user)
    sb = get_service_client()
    row = await sector_news_service.upsert_news(
        sb,
        tenant_id=tenant_id,
        news_id=None,
        ateco_2digit=payload.ateco_2digit,
        headline=payload.headline,
        body=payload.body,
        source_url=payload.source_url,
    )
    if row is None:
        raise HTTPException(status_code=500, detail="insert_failed")
    return row


@router.patch("/{news_id}")
async def update_sector_news(
    news_id: str, payload: SectorNewsUpdate, user: CurrentUser
) -> dict[str, Any]:
    """Update a tenant-owned sector-news row.

    Global rows (tenant_id IS NULL) are read-only — return 404.
    """
    tenant_id = require_tenant(user)
    sb = get_service_client()

    # Fetch existing to merge nulls + verify ownership.
    res = (
        sb.table("sector_news")
        .select("*")
        .eq("id", news_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    existing = (res.data or [None])[0]
    if existing is None:
        raise HTTPException(status_code=404, detail="not_found_or_global")

    merged_status = payload.status or existing.get("status", "active")
    row = await sector_news_service.upsert_news(
        sb,
        tenant_id=tenant_id,
        news_id=news_id,
        ateco_2digit=payload.ateco_2digit or existing["ateco_2digit"],
        headline=payload.headline or existing["headline"],
        body=payload.body or existing["body"],
        source_url=payload.source_url
        if payload.source_url is not None
        else existing.get("source_url"),
        status=merged_status,
    )
    if row is None:
        raise HTTPException(status_code=500, detail="update_failed")
    return row


@router.delete("/{news_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_sector_news(news_id: str, user: CurrentUser) -> None:
    """Soft-archive a tenant-owned row. Global seeds cannot be deleted."""
    tenant_id = require_tenant(user)
    sb = get_service_client()
    ok = await sector_news_service.archive_news(
        sb, tenant_id=tenant_id, news_id=news_id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="not_found_or_global")
