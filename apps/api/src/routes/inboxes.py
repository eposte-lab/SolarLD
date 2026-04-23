"""Tenant inboxes — CRUD for multi-inbox email sending.

Each inbox represents a distinct ``From`` address on the tenant's
verified domain.  The InboxSelector in ``inbox_service.py`` picks the
best available inbox for every outreach send using round-robin + daily
cap enforcement.

Endpoints
---------
GET  /v1/inboxes              List tenant inboxes + live usage stats
POST /v1/inboxes              Create a new inbox
PATCH /v1/inboxes/{id}        Update inbox fields
POST /v1/inboxes/{id}/unpause Manually clear the auto-pause flag
DELETE /v1/inboxes/{id}       Hard delete (or soft-deactivate)
GET  /v1/inboxes/quota        Live capacity summary across all inboxes
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..services import inbox_service

router = APIRouter()
log = get_logger(__name__)

_SELECT_FIELDS = (
    "id, tenant_id, email, display_name, reply_to_email, "
    "signature_html, daily_cap, paused_until, pause_reason, "
    "sent_date, total_sent_today, last_sent_at, active, "
    "created_at, updated_at"
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class InboxCreate(BaseModel):
    email: EmailStr
    display_name: str = Field(default="", max_length=120)
    reply_to_email: EmailStr | None = None
    signature_html: str | None = Field(default=None, max_length=8000)
    daily_cap: int = Field(default=50, ge=1, le=2000)


class InboxUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    reply_to_email: EmailStr | None = None
    signature_html: str | None = Field(default=None, max_length=8000)
    daily_cap: int | None = Field(default=None, ge=1, le=2000)
    active: bool | None = None


# ---------------------------------------------------------------------------
# GET /v1/inboxes
# ---------------------------------------------------------------------------


@router.get("")
async def list_inboxes(ctx: CurrentUser) -> dict[str, Any]:
    """List all inboxes for the current tenant.

    Returns live stats (``total_sent_today``, ``paused_until``) so the
    dashboard can show capacity at-a-glance without a separate quota call.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    try:
        res = (
            sb.table("tenant_inboxes")
            .select(_SELECT_FIELDS)
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=False)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("inboxes.list_failed", tenant_id=tenant_id, err=str(exc))
        return {"inboxes": [], "total": 0}

    rows = res.data or []
    today = datetime.now(timezone.utc).date().isoformat()

    # Annotate each inbox with derived fields for the UI.
    for row in rows:
        # If sent_date is not today, the counter is stale — show 0.
        if row.get("sent_date") != today:
            row["total_sent_today"] = 0
        now_utc = datetime.now(timezone.utc).isoformat()
        paused_until = row.get("paused_until")
        row["is_paused"] = bool(paused_until and paused_until > now_utc)
        remaining = max(0, int(row.get("daily_cap", 50)) - int(row.get("total_sent_today", 0)))
        row["remaining_today"] = remaining

    return {"inboxes": rows, "total": len(rows)}


# ---------------------------------------------------------------------------
# GET /v1/inboxes/quota
# ---------------------------------------------------------------------------


@router.get("/quota")
async def get_inbox_quota(ctx: CurrentUser) -> dict[str, Any]:
    """Aggregate capacity summary: total cap, used, remaining, paused count.

    Used by the settings overview card to show "X / Y sends today".
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    today = datetime.now(timezone.utc).date().isoformat()
    now_utc = datetime.now(timezone.utc).isoformat()

    try:
        res = (
            sb.table("tenant_inboxes")
            .select("daily_cap, sent_date, total_sent_today, paused_until, active")
            .eq("tenant_id", tenant_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("inboxes.quota_failed", tenant_id=tenant_id, err=str(exc))
        return {
            "total_daily_cap": 0,
            "sent_today": 0,
            "remaining_today": 0,
            "active_inboxes": 0,
            "paused_inboxes": 0,
        }

    rows = res.data or []
    active = [r for r in rows if r.get("active")]
    paused = [
        r for r in active
        if r.get("paused_until") and r["paused_until"] > now_utc
    ]

    total_cap = sum(int(r.get("daily_cap", 50)) for r in active)
    sent = sum(
        int(r.get("total_sent_today", 0))
        for r in active
        if r.get("sent_date") == today
    )

    return {
        "total_daily_cap": total_cap,
        "sent_today": sent,
        "remaining_today": max(0, total_cap - sent),
        "active_inboxes": len(active),
        "paused_inboxes": len(paused),
    }


# ---------------------------------------------------------------------------
# POST /v1/inboxes
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_inbox(body: InboxCreate, ctx: CurrentUser) -> dict[str, Any]:
    """Create a new sending inbox.

    The email must be on the tenant's verified domain (enforced by the
    domain check in the email_from_domain settings). Duplicate emails
    within the same tenant return 409 Conflict.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    insert_data: dict[str, Any] = {
        "tenant_id": tenant_id,
        "email": str(body.email).lower().strip(),
        "display_name": body.display_name.strip(),
        "daily_cap": body.daily_cap,
        "active": True,
    }
    if body.reply_to_email:
        insert_data["reply_to_email"] = str(body.reply_to_email).lower().strip()
    if body.signature_html:
        insert_data["signature_html"] = body.signature_html

    try:
        res = sb.table("tenant_inboxes").insert(insert_data).execute()
    except Exception as exc:  # noqa: BLE001
        err_str = str(exc)
        if "unique" in err_str.lower() or "duplicate" in err_str.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Inbox with email {body.email} already exists for this tenant",
            ) from exc
        log.warning("inboxes.create_failed", tenant_id=tenant_id, err=err_str)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create inbox",
        ) from exc

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Insert returned no data",
        )

    log.info("inboxes.created", tenant_id=tenant_id, inbox_id=res.data[0]["id"])
    return res.data[0]


# ---------------------------------------------------------------------------
# PATCH /v1/inboxes/{inbox_id}
# ---------------------------------------------------------------------------


@router.patch("/{inbox_id}")
async def update_inbox(
    inbox_id: str,
    body: InboxUpdate,
    ctx: CurrentUser,
) -> dict[str, Any]:
    """Update an inbox's display name, cap, signature, or active state."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    update_data: dict[str, Any] = {
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    if body.display_name is not None:
        update_data["display_name"] = body.display_name.strip()
    if body.reply_to_email is not None:
        update_data["reply_to_email"] = str(body.reply_to_email).lower().strip()
    if body.signature_html is not None:
        update_data["signature_html"] = body.signature_html
    if body.daily_cap is not None:
        update_data["daily_cap"] = body.daily_cap
    if body.active is not None:
        update_data["active"] = body.active

    if len(update_data) == 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields to update",
        )

    try:
        res = (
            sb.table("tenant_inboxes")
            .update(update_data)
            .eq("id", inbox_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("inboxes.update_failed", inbox_id=inbox_id, err=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Update failed",
        ) from exc

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inbox not found or not owned by this tenant",
        )

    return res.data[0]


# ---------------------------------------------------------------------------
# POST /v1/inboxes/{inbox_id}/unpause
# ---------------------------------------------------------------------------


@router.post("/{inbox_id}/unpause")
async def unpause_inbox(inbox_id: str, ctx: CurrentUser) -> dict[str, Any]:
    """Manually clear the auto-pause flag on an inbox.

    Useful when the operator has resolved the issue (e.g. confirmed the
    Resend sender is healthy again) and wants to re-activate immediately
    without waiting for the pause window to expire.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    updated = await inbox_service.unpause_inbox(sb, inbox_id, tenant_id=tenant_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inbox not found or not owned by this tenant",
        )
    return {"ok": True, "inbox_id": inbox_id}


# ---------------------------------------------------------------------------
# DELETE /v1/inboxes/{inbox_id}
# ---------------------------------------------------------------------------


@router.delete("/{inbox_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_inbox(inbox_id: str, ctx: CurrentUser) -> Response:
    """Permanently delete an inbox.

    Historical ``campaigns`` rows referencing this inbox will have their
    ``inbox_id`` set to NULL (ON DELETE SET NULL in the migration), so
    deliverability analytics are preserved for historical sends.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    try:
        res = (
            sb.table("tenant_inboxes")
            .delete()
            .eq("id", inbox_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("inboxes.delete_failed", inbox_id=inbox_id, err=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Delete failed",
        ) from exc

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inbox not found or not owned by this tenant",
        )
    log.info("inboxes.deleted", tenant_id=tenant_id, inbox_id=inbox_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
