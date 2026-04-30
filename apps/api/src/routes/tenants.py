"""Tenant self-service endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client

log = get_logger(__name__)
router = APIRouter()


@router.get("/me")
async def get_my_tenant(ctx: CurrentUser) -> dict[str, object]:
    """Return the caller's tenant record."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = sb.table("tenants").select("*").eq("id", tenant_id).limit(1).execute()
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account non trovato.",
        )
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
        "followup_from_email",
        "settings",
        # Legal fields (0052 + 0082) — required by GDPR footer (legal_*)
        # and GSE practices (codice_fiscale, numero_cciaa, responsabile
        # tecnico). Settings dashboard's "Dati legali" page hits this.
        "legal_name",
        "legal_address",
        "vat_number",
        "codice_fiscale",
        "numero_cciaa",
        "responsabile_tecnico_nome",
        "responsabile_tecnico_cognome",
        "responsabile_tecnico_codice_fiscale",
        "responsabile_tecnico_qualifica",
        "responsabile_tecnico_iscrizione_albo",
    }
    update = {k: v for k, v in payload.items() if k in allowed}
    if not update:
        raise HTTPException(status_code=400, detail="No updatable fields provided")

    sb = get_service_client()
    res = sb.table("tenants").update(update).eq("id", tenant_id).execute()
    return {"ok": True, "data": res.data}


@router.get("/me/usage")
async def get_usage(ctx: CurrentUser) -> dict[str, object]:
    """Month-to-date usage stats, aggregated server-side.

    Delegates to the ``analytics_usage_mtd`` Postgres function which
    rolls up roofs/leads/campaigns/api_usage_log for the current
    calendar month. Keys match the shape the dashboard expects so the
    pre-existing Settings widget continues to work unchanged.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    try:
        res = sb.rpc("analytics_usage_mtd", {"p_tenant_id": tenant_id}).execute()
    except Exception as exc:
        log.warning("tenants.usage_mtd_failed", tenant_id=tenant_id, err=str(exc))
        raise HTTPException(
            status_code=502,
            detail="Statistiche di utilizzo temporaneamente non disponibili. Riprova tra qualche minuto.",
        ) from exc
    return res.data or {
        "roofs_scanned_mtd": 0,
        "leads_generated_mtd": 0,
        "emails_sent_mtd": 0,
        "postcards_sent_mtd": 0,
        "total_cost_eur": 0.0,
    }
