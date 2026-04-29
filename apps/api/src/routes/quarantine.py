"""Quarantine review — ops endpoints for content-validation blocked emails.

The ``quarantine_emails`` table (migration 0061) holds emails that were
blocked by ``content_validator.py`` before send. This router lets ops
(or the tenant admin, as long as they are authenticated) review, approve,
or reject each blocked item.

Endpoints
---------
GET    /v1/quarantine              List quarantine items (filterable by status)
GET    /v1/quarantine/{id}         Get a single item with full HTML/text
POST   /v1/quarantine/{id}/approve Mark as approved (optionally add notes)
POST   /v1/quarantine/{id}/reject  Mark as rejected  (optionally add notes)

Approval workflow
-----------------
Approving a quarantine row does NOT automatically re-send the email — the
OutreachAgent processes its normal follow-up schedule. The ``review_status``
flag simply lifts the block: the next time the follow-up cron picks up the
lead, if content validation passes again the email will go through. For
immediate re-send the ops user should use the dashboard "Re-send" button on
the lead detail page.

Security
--------
RLS on ``quarantine_emails`` scopes SELECT to the authenticated tenant.
All write mutations use the service-role client so the checked row's
``reviewed_by`` is recorded as the request's user ID.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client

router = APIRouter()
log = get_logger(__name__)

_LIST_COLUMNS = (
    "id, tenant_id, lead_id, subject, text_snippet, html_snippet, "
    "email_style, sequence_step, validation_score, violations, "
    "auto_decision, review_status, reviewed_at, review_notes, "
    "resent_at, resent_outreach_id, created_at, updated_at"
)

_VALID_STATUSES = {"pending_review", "approved", "rejected"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ReviewBody(BaseModel):
    """Payload for approve / reject actions."""
    notes: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_quarantine(
    ctx: CurrentUser,
    review_status: str | None = Query(default="pending_review"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List quarantine items for the current tenant.

    Query params:
      - ``review_status``: one of ``pending_review``, ``approved``, ``rejected``
        (default: ``pending_review``). Pass ``all`` to skip the filter.
      - ``page`` / ``page_size``: pagination (max 200 per page).
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    offset = (page - 1) * page_size

    q = (
        sb.table("quarantine_emails")
        .select(_LIST_COLUMNS, count="exact")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .range(offset, offset + page_size - 1)
    )

    if review_status and review_status != "all":
        if review_status not in _VALID_STATUSES:
            valid = ", ".join(sorted(_VALID_STATUSES))
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Stato revisione non valido. Valori ammessi: {valid} "
                    "oppure 'all'."
                ),
            )
        q = q.eq("review_status", review_status)

    try:
        res = q.execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("quarantine.list_failed", tenant_id=tenant_id, err=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Lista quarantena non disponibile in questo momento. Riprova tra qualche minuto.",
        )

    return {
        "items": res.data or [],
        "total": res.count or 0,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{quarantine_id}")
async def get_quarantine_item(
    quarantine_id: str,
    ctx: CurrentUser,
) -> dict[str, Any]:
    """Fetch a single quarantine item (full HTML for preview)."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    res = (
        sb.table("quarantine_emails")
        .select(_LIST_COLUMNS)
        .eq("id", quarantine_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    row = (res.data or [None])[0]
    if not row:
        raise HTTPException(status_code=404, detail="quarantine item not found")
    return row


@router.post("/{quarantine_id}/approve", status_code=status.HTTP_200_OK)
async def approve_quarantine(
    quarantine_id: str,
    ctx: CurrentUser,
    body: ReviewBody = ReviewBody(),
) -> dict[str, Any]:
    """Mark a quarantine item as approved.

    Sets ``review_status = 'approved'``, records ``reviewed_at`` and
    ``reviewed_by`` (the caller's user ID). The send will be eligible
    for the next cron run.

    Idempotent: calling approve on an already-approved item is a no-op
    (returns the current state).
    """
    tenant_id = require_tenant(ctx)
    user_id = ctx.user.id if ctx.user else None
    sb = get_service_client()

    now_iso = datetime.now(timezone.utc).isoformat()

    update_payload: dict[str, Any] = {
        "review_status": "approved",
        "reviewed_at": now_iso,
        "updated_at": now_iso,
    }
    if user_id:
        update_payload["reviewed_by"] = str(user_id)
    if body.notes is not None:
        update_payload["review_notes"] = body.notes

    res = (
        sb.table("quarantine_emails")
        .update(update_payload)
        .eq("id", quarantine_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    row = (res.data or [None])[0]
    if not row:
        raise HTTPException(
            status_code=404,
            detail="quarantine item not found or already processed",
        )

    log.info(
        "quarantine.approved",
        quarantine_id=quarantine_id,
        tenant_id=tenant_id,
        reviewed_by=user_id,
    )
    return row


@router.post("/{quarantine_id}/reject", status_code=status.HTTP_200_OK)
async def reject_quarantine(
    quarantine_id: str,
    ctx: CurrentUser,
    body: ReviewBody = ReviewBody(),
) -> dict[str, Any]:
    """Mark a quarantine item as rejected (permanently blocked).

    The lead's send attempt will not be retried for this sequence step.
    """
    tenant_id = require_tenant(ctx)
    user_id = ctx.user.id if ctx.user else None
    sb = get_service_client()

    now_iso = datetime.now(timezone.utc).isoformat()

    update_payload: dict[str, Any] = {
        "review_status": "rejected",
        "reviewed_at": now_iso,
        "updated_at": now_iso,
    }
    if user_id:
        update_payload["reviewed_by"] = str(user_id)
    if body.notes is not None:
        update_payload["review_notes"] = body.notes

    res = (
        sb.table("quarantine_emails")
        .update(update_payload)
        .eq("id", quarantine_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    row = (res.data or [None])[0]
    if not row:
        raise HTTPException(
            status_code=404,
            detail="quarantine item not found or already processed",
        )

    log.info(
        "quarantine.rejected",
        quarantine_id=quarantine_id,
        tenant_id=tenant_id,
        reviewed_by=user_id,
    )
    return row
