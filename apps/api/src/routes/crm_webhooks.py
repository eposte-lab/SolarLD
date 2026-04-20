"""Outbound CRM webhook subscription management.

Tenants register HTTPS endpoints here. When lifecycle events fire
(``lead.contract_signed``, ``lead.scored``, ...) the API enqueues a
``crm_webhook_task`` that fans out a signed POST to each active
subscription. Receivers verify authenticity via the
``X-SolarLead-Signature`` header (HMAC-SHA256 of the body using the
per-subscription secret).
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, HttpUrl

from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..services.crm_webhook_service import SUPPORTED_EVENTS

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class WebhookCreate(BaseModel):
    """Inbound body for ``POST /v1/crm-webhooks``."""

    label: str = Field(min_length=1, max_length=120)
    url: HttpUrl
    events: list[str] | None = None

    @classmethod
    def _validate_events(cls, events: list[str]) -> list[str]:
        bad = [e for e in events if e not in SUPPORTED_EVENTS]
        if bad:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unsupported events: {bad}. "
                    f"Allowed: {sorted(SUPPORTED_EVENTS)}"
                ),
            )
        return events


class WebhookUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    url: HttpUrl | None = None
    events: list[str] | None = None
    active: bool | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_webhooks(ctx: CurrentUser) -> list[dict[str, Any]]:
    """List this tenant's CRM webhook subscriptions.

    The ``secret`` field is intentionally masked; operators can only
    see it at creation time. If they lose it they can rotate by
    calling ``POST /{id}/rotate-secret``.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("crm_webhook_subscriptions")
        .select(
            "id, label, url, events, active, last_status, "
            "last_delivered_at, failure_count, created_at, updated_at"
        )
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data or []


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_webhook(
    ctx: CurrentUser, payload: WebhookCreate
) -> dict[str, Any]:
    """Register a new CRM webhook. Returns the secret ONCE."""
    tenant_id = require_tenant(ctx)
    events = payload.events or sorted(SUPPORTED_EVENTS)
    WebhookCreate._validate_events(events)

    secret = secrets.token_urlsafe(32)
    sb = get_service_client()
    res = (
        sb.table("crm_webhook_subscriptions")
        .insert(
            {
                "tenant_id": tenant_id,
                "label": payload.label,
                "url": str(payload.url),
                "secret": secret,
                "events": events,
            }
        )
        .execute()
    )
    row = (res.data or [{}])[0]
    # Return the secret once — operator must copy it now.
    return {
        "id": row.get("id"),
        "label": row.get("label"),
        "url": row.get("url"),
        "events": row.get("events"),
        "active": row.get("active"),
        "secret": secret,
        "created_at": row.get("created_at"),
    }


@router.patch("/{webhook_id}")
async def update_webhook(
    ctx: CurrentUser, webhook_id: str, payload: WebhookUpdate
) -> dict[str, Any]:
    tenant_id = require_tenant(ctx)
    update: dict[str, Any] = {}
    if payload.label is not None:
        update["label"] = payload.label
    if payload.url is not None:
        update["url"] = str(payload.url)
    if payload.events is not None:
        WebhookCreate._validate_events(payload.events)
        update["events"] = payload.events
    if payload.active is not None:
        update["active"] = payload.active
        # Re-activating manually clears the circuit breaker.
        if payload.active:
            update["failure_count"] = 0
    if not update:
        raise HTTPException(status_code=400, detail="no updatable fields")

    sb = get_service_client()
    res = (
        sb.table("crm_webhook_subscriptions")
        .update(update)
        .eq("id", webhook_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="webhook not found")
    return res.data[0]


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(ctx: CurrentUser, webhook_id: str):
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    sb.table("crm_webhook_subscriptions").delete().eq("id", webhook_id).eq(
        "tenant_id", tenant_id
    ).execute()
    from fastapi import Response

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{webhook_id}/rotate-secret")
async def rotate_secret(ctx: CurrentUser, webhook_id: str) -> dict[str, str]:
    """Generate a fresh signing secret. Returned once; old secret invalidated."""
    tenant_id = require_tenant(ctx)
    new_secret = secrets.token_urlsafe(32)
    sb = get_service_client()
    res = (
        sb.table("crm_webhook_subscriptions")
        .update({"secret": new_secret})
        .eq("id", webhook_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="webhook not found")
    return {"id": webhook_id, "secret": new_secret}


@router.get("/{webhook_id}/deliveries")
async def list_deliveries(
    ctx: CurrentUser,
    webhook_id: str,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Last N delivery attempts for a subscription — debugging aid."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    # Confirm ownership before returning deliveries.
    owner = (
        sb.table("crm_webhook_subscriptions")
        .select("id")
        .eq("id", webhook_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    if not owner.data:
        raise HTTPException(status_code=404, detail="webhook not found")

    res = (
        sb.table("crm_webhook_deliveries")
        .select(
            "id, event_type, attempt, status_code, error, occurred_at"
        )
        .eq("subscription_id", webhook_id)
        .order("occurred_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


@router.get("/events/supported")
async def supported_events() -> dict[str, Any]:
    """Return the event catalogue so the dashboard can render a picker."""
    return {"events": sorted(SUPPORTED_EVENTS)}
