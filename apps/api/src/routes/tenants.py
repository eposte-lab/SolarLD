"""Tenant self-service endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client

router = APIRouter()


@router.get("/me")
async def get_my_tenant(ctx: CurrentUser) -> dict[str, object]:
    """Return the caller's tenant record."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = sb.table("tenants").select("*").eq("id", tenant_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return res.data[0]


@router.patch("/me")
async def update_my_tenant(ctx: CurrentUser, payload: dict[str, object]) -> dict[str, object]:
    """Update tenant brand / WhatsApp / settings."""
    tenant_id = require_tenant(ctx)

    # Whitelist updatable fields to prevent privilege escalation
    allowed = {
        "business_name",
        "contact_email",
        "contact_phone",
        "whatsapp_number",
        "brand_logo_url",
        "brand_primary_color",
        "email_from_domain",
        "email_from_name",
        "settings",
    }
    update = {k: v for k, v in payload.items() if k in allowed}
    if not update:
        raise HTTPException(status_code=400, detail="No updatable fields provided")

    sb = get_service_client()
    res = sb.table("tenants").update(update).eq("id", tenant_id).execute()
    return {"ok": True, "data": res.data}


@router.get("/me/usage")
async def get_usage(ctx: CurrentUser) -> dict[str, object]:
    """Return month-to-date usage stats (stub)."""
    require_tenant(ctx)
    # TODO: aggregate from api_usage_log
    return {
        "roofs_scanned_mtd": 0,
        "leads_generated_mtd": 0,
        "emails_sent_mtd": 0,
        "postcards_sent_mtd": 0,
        "total_cost_eur": 0.0,
    }
