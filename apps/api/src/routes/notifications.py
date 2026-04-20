"""In-app notifications — bell icon data source.

Surface:

    GET   /v1/notifications?unread_only=true&limit=50
    GET   /v1/notifications/count     — unread counter for the bell
    POST  /v1/notifications/mark-read — body: {"ids": [...]} or {"all": true}
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client

router = APIRouter()


@router.get("")
async def list_notifications(
    ctx: CurrentUser,
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Return notifications visible to the caller.

    RLS-equivalent filter applied manually because we go through the
    service client: notifications targeted at this user OR broadcast
    (user_id IS NULL) for the caller's tenant.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    query = (
        sb.table("notifications")
        .select("*")
        .eq("tenant_id", tenant_id)
        .or_(f"user_id.eq.{ctx.user_id},user_id.is.null")
    )
    if unread_only:
        query = query.is_("read_at", "null")
    res = query.order("created_at", desc=True).limit(limit).execute()
    return res.data or []


@router.get("/count")
async def unread_count(ctx: CurrentUser) -> dict[str, int]:
    """Unread notification count — rendered as the bell badge."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("notifications")
        .select("id", count="exact", head=True)
        .eq("tenant_id", tenant_id)
        .is_("read_at", "null")
        .or_(f"user_id.eq.{ctx.user_id},user_id.is.null")
        .execute()
    )
    return {"unread": res.count or 0}


class MarkReadPayload(BaseModel):
    ids: list[str] | None = None
    all: bool = False


@router.post("/mark-read")
async def mark_read(
    ctx: CurrentUser, payload: MarkReadPayload
) -> dict[str, Any]:
    """Mark a set of notifications read, or all of them at once."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    query = (
        sb.table("notifications")
        .update({"read_at": "now()"})
        .eq("tenant_id", tenant_id)
        .or_(f"user_id.eq.{ctx.user_id},user_id.is.null")
        .is_("read_at", "null")
    )
    if payload.all:
        res = query.execute()
    elif payload.ids:
        res = query.in_("id", payload.ids).execute()
    else:
        return {"ok": False, "reason": "no_ids_or_all"}

    return {"ok": True, "updated": len(res.data or [])}
