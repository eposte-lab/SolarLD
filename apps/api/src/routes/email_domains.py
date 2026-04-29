"""Tenant email domains — CRUD + live DNS verification.

Endpoints
---------
GET    /v1/email-domains                  List all domains for the tenant
POST   /v1/email-domains                  Add a brand or outreach domain
PATCH  /v1/email-domains/{id}             Update fields (tracking_host, daily_soft_cap, etc.)
DELETE /v1/email-domains/{id}             Remove a domain (only if no active inboxes)
POST   /v1/email-domains/{id}/dns-check   Run live DNS check → returns DnsVerificationResult
POST   /v1/email-domains/{id}/pause       Pause all sends from this domain
POST   /v1/email-domains/{id}/unpause     Unpause
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..services.dns_verification_service import verify_domain

router = APIRouter()
log = get_logger(__name__)

_SELECT_FIELDS = (
    "id, tenant_id, domain, purpose, default_provider, tracking_host, "
    "resend_domain_id, verified_at, spf_verified_at, dkim_verified_at, "
    "dmarc_verified_at, tracking_cname_verified_at, dmarc_policy, "
    "daily_soft_cap, paused_until, pause_reason, last_dns_check_at, "
    "active, created_at, updated_at"
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class DomainCreate(BaseModel):
    domain: str = Field(..., min_length=4, max_length=253)
    purpose: str = Field(default="outreach")
    default_provider: str = Field(default="resend")
    tracking_host: str | None = Field(default=None, max_length=253)
    daily_soft_cap: int = Field(default=300, ge=1, le=10000)
    resend_domain_id: str | None = None

    @field_validator("purpose")
    @classmethod
    def _validate_purpose(cls, v: str) -> str:
        if v not in ("brand", "outreach"):
            raise ValueError("purpose must be 'brand' or 'outreach'")
        return v

    @field_validator("default_provider")
    @classmethod
    def _validate_provider(cls, v: str) -> str:
        if v not in ("resend", "gmail_oauth", "m365_oauth", "smtp"):
            raise ValueError("invalid provider")
        return v

    @field_validator("domain")
    @classmethod
    def _normalise_domain(cls, v: str) -> str:
        return v.lower().strip().lstrip("@")


class DomainUpdate(BaseModel):
    tracking_host: str | None = Field(default=None, max_length=253)
    daily_soft_cap: int | None = Field(default=None, ge=1, le=10000)
    default_provider: str | None = None
    resend_domain_id: str | None = None
    active: bool | None = None

    @field_validator("default_provider")
    @classmethod
    def _validate_provider(cls, v: str | None) -> str | None:
        if v is not None and v not in ("resend", "gmail_oauth", "m365_oauth", "smtp"):
            raise ValueError("invalid provider")
        return v


# ---------------------------------------------------------------------------
# GET /v1/email-domains
# ---------------------------------------------------------------------------


@router.get("")
async def list_email_domains(ctx: CurrentUser) -> dict[str, Any]:
    """List all email domains for the tenant, ordered by purpose then domain."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    try:
        res = (
            sb.table("tenant_email_domains")
            .select(_SELECT_FIELDS)
            .eq("tenant_id", tenant_id)
            .order("purpose", desc=False)
            .order("domain", desc=False)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("email_domains.list_failed", tenant_id=tenant_id, err=str(exc))
        return {"domains": [], "total": 0}

    rows = res.data or []
    now_utc = datetime.now(timezone.utc).isoformat()
    for row in rows:
        paused_until = row.get("paused_until")
        row["is_paused"] = bool(paused_until and paused_until > now_utc)

    return {"domains": rows, "total": len(rows)}


# ---------------------------------------------------------------------------
# POST /v1/email-domains
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_email_domain(body: DomainCreate, ctx: CurrentUser) -> dict[str, Any]:
    """Add a new email domain.

    The domain must be unique per tenant. At most one 'brand' domain is
    currently enforced by convention (not constraint) — ops can override.
    Duplication returns 409 Conflict.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    insert_data: dict[str, Any] = {
        "tenant_id": tenant_id,
        "domain": body.domain,
        "purpose": body.purpose,
        "default_provider": body.default_provider,
        "daily_soft_cap": body.daily_soft_cap,
        "active": True,
    }
    if body.tracking_host:
        insert_data["tracking_host"] = body.tracking_host.lower().strip()
    if body.resend_domain_id:
        insert_data["resend_domain_id"] = body.resend_domain_id

    try:
        res = sb.table("tenant_email_domains").insert(insert_data).execute()
    except Exception as exc:  # noqa: BLE001
        err_str = str(exc)
        if "unique" in err_str.lower() or "duplicate" in err_str.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Il dominio {body.domain} è già configurato per questo "
                    "account."
                ),
            ) from exc
        log.warning("email_domains.create_failed", tenant_id=tenant_id, err=err_str)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Creazione del dominio non riuscita. Riprova tra qualche minuto.",
        ) from exc

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Insert returned no data",
        )
    log.info("email_domains.created", tenant_id=tenant_id, domain=body.domain)
    return res.data[0]


# ---------------------------------------------------------------------------
# PATCH /v1/email-domains/{domain_id}
# ---------------------------------------------------------------------------


@router.patch("/{domain_id}")
async def update_email_domain(
    domain_id: str, body: DomainUpdate, ctx: CurrentUser
) -> dict[str, Any]:
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    update_data: dict[str, Any] = {
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    if body.tracking_host is not None:
        update_data["tracking_host"] = (
            body.tracking_host.lower().strip() if body.tracking_host else None
        )
    if body.daily_soft_cap is not None:
        update_data["daily_soft_cap"] = body.daily_soft_cap
    if body.default_provider is not None:
        update_data["default_provider"] = body.default_provider
    if body.resend_domain_id is not None:
        update_data["resend_domain_id"] = body.resend_domain_id
    if body.active is not None:
        update_data["active"] = body.active

    if len(update_data) == 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields to update",
        )

    try:
        res = (
            sb.table("tenant_email_domains")
            .update(update_data)
            .eq("id", domain_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("email_domains.update_failed", domain_id=domain_id, err=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Update failed",
        ) from exc

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Domain not found or not owned by this tenant",
        )
    return res.data[0]


# ---------------------------------------------------------------------------
# DELETE /v1/email-domains/{domain_id}
# ---------------------------------------------------------------------------


@router.delete("/{domain_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_email_domain(domain_id: str, ctx: CurrentUser) -> Response:
    """Delete a domain. Fails 409 if active inboxes are still linked."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # Guard: don't delete if active inboxes reference this domain.
    inbox_res = (
        sb.table("tenant_inboxes")
        .select("id", count="exact")
        .eq("domain_id", domain_id)
        .eq("active", True)
        .execute()
    )
    if inbox_res.count and inbox_res.count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Domain has {inbox_res.count} active inbox(es). "
                "Deactivate or re-assign them first."
            ),
        )

    try:
        res = (
            sb.table("tenant_email_domains")
            .delete()
            .eq("id", domain_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("email_domains.delete_failed", domain_id=domain_id, err=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Delete failed",
        ) from exc

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Domain not found or not owned by this tenant",
        )
    log.info("email_domains.deleted", domain_id=domain_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# POST /v1/email-domains/{domain_id}/dns-check
# ---------------------------------------------------------------------------


@router.post("/{domain_id}/dns-check")
async def run_dns_check(domain_id: str, ctx: CurrentUser) -> dict[str, Any]:
    """Run a live DNS check for this domain.

    Queries SPF / DKIM (Resend + Google) / DMARC / tracking CNAME in
    parallel. Updates the verified_* timestamps when records are valid.
    Returns the full DnsVerificationResult as JSON.

    Idempotent and safe to call as often as needed — DNS lookups are
    read-only. The UI calls this on "Verify now" button press.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    res = (
        sb.table("tenant_email_domains")
        .select("id, domain, tracking_host, resend_domain_id, purpose")
        .eq("id", domain_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Domain not found or not owned by this tenant",
        )

    row = res.data[0]
    domain = row["domain"]
    tracking_host = row.get("tracking_host")

    verification = await verify_domain(
        domain,
        tracking_host=tracking_host,
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    update_fields: dict[str, Any] = {"last_dns_check_at": now_iso, "updated_at": now_iso}

    if verification.spf.ok:
        update_fields["spf_verified_at"] = now_iso
    if verification.dkim_resend.ok or verification.dkim_google.ok:
        update_fields["dkim_verified_at"] = now_iso
    if verification.dmarc.ok:
        update_fields["dmarc_verified_at"] = now_iso
        if verification.dmarc_policy:
            update_fields["dmarc_policy"] = verification.dmarc_policy
    if tracking_host and verification.tracking_cname.ok:
        update_fields["tracking_cname_verified_at"] = now_iso
    if verification.all_critical_ok:
        update_fields["verified_at"] = now_iso

    try:
        sb.table("tenant_email_domains").update(update_fields).eq("id", domain_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("email_domains.dns_check_persist_failed", domain_id=domain_id, err=str(exc))
        # Don't fail the whole response — the check result is still valuable.

    return {"domain_id": domain_id, **verification.to_dict()}


# ---------------------------------------------------------------------------
# POST /v1/email-domains/{domain_id}/pause
# ---------------------------------------------------------------------------


@router.post("/{domain_id}/pause")
async def pause_email_domain(
    domain_id: str,
    ctx: CurrentUser,
    hours: int = Query(default=24, ge=1, le=168),
    reason: str = Query(default="manual_pause"),
) -> dict[str, Any]:
    """Pause all outreach sends from this domain for ``hours`` hours."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        res = (
            sb.table("tenant_email_domains")
            .update({"paused_until": until, "pause_reason": reason, "updated_at": now_iso})
            .eq("id", domain_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Pause failed") from exc
    if not res.data:
        raise HTTPException(status_code=404, detail="Domain not found")
    return {"ok": True, "paused_until": until}


# ---------------------------------------------------------------------------
# POST /v1/email-domains/{domain_id}/unpause
# ---------------------------------------------------------------------------


@router.post("/{domain_id}/unpause")
async def unpause_email_domain(domain_id: str, ctx: CurrentUser) -> dict[str, Any]:
    """Manually clear the pause on a domain."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        res = (
            sb.table("tenant_email_domains")
            .update({"paused_until": None, "pause_reason": None, "updated_at": now_iso})
            .eq("id", domain_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Unpause failed") from exc
    if not res.data:
        raise HTTPException(status_code=404, detail="Domain not found")
    return {"ok": True, "domain_id": domain_id}
