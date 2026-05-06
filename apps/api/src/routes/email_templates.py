"""Email Templates — HTTP surface for generic_outreach custom email templates.

Operators design HTML emails in the dashboard editor, store them here,
then associate them with a prospect_list whose campaign_type='generic_outreach'.
When outreach is launched the OutreachAgent renders the stored HTML with
Jinja2 variable substitution instead of the standard Solar template family.

Endpoints
---------
GET    /v1/email-templates              List templates for current tenant
POST   /v1/email-templates              Create a new template
GET    /v1/email-templates/{id}         Get one template
PATCH  /v1/email-templates/{id}         Update name / subject / html
DELETE /v1/email-templates/{id}         Hard-delete (lists lose FK via ON DELETE SET NULL)
POST   /v1/email-templates/{id}/preview Render with sample data → returns HTML string
POST   /v1/email-templates/validate     Validate HTML without saving (syntax + GDPR check)
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client

router = APIRouter()
log = get_logger(__name__)

# ---------------------------------------------------------------------------
# GDPR-required variables that must appear in every custom template.
# ---------------------------------------------------------------------------

REQUIRED_VARS: frozenset[str] = frozenset({
    "unsubscribe_url",
    "tenant_legal_name",
    "tenant_vat_number",
    "tenant_legal_address",
})

# All documented variables for the variable-picker UI.
ALL_VARS: list[dict[str, str]] = [
    # Contact
    {"slug": "greeting_name",        "label": "Nome contatto",        "example": "Mario Rossi"},
    {"slug": "business_name",        "label": "Nome azienda",         "example": "Rossi S.r.l."},
    {"slug": "hq_address",           "label": "Indirizzo",            "example": "Via Roma 1"},
    {"slug": "hq_cap",               "label": "CAP",                  "example": "80100"},
    {"slug": "hq_city",              "label": "Città",                "example": "Napoli"},
    {"slug": "hq_province",          "label": "Provincia",            "example": "NA"},
    {"slug": "phone",                "label": "Telefono",             "example": "+39 081 1234567"},
    {"slug": "recipient_email",      "label": "Email destinatario",   "example": "mario@rossi.it"},
    # Sender
    {"slug": "sender_first_name",    "label": "Nome mittente",        "example": "Alfonso"},
    {"slug": "tenant_name",          "label": "Nome brand/azienda",   "example": "SolarTech"},
    {"slug": "brand_logo_url",       "label": "Logo brand (URL)",     "example": "https://..."},
    # GDPR (required)
    {"slug": "unsubscribe_url",      "label": "Link disiscrizione ✱", "example": "https://..."},
    {"slug": "tenant_legal_name",    "label": "Ragione sociale ✱",    "example": "SolarTech S.r.l."},
    {"slug": "tenant_vat_number",    "label": "P.IVA ✱",              "example": "IT12345678901"},
    {"slug": "tenant_legal_address", "label": "Sede legale ✱",        "example": "Via Roma 1, 00100 Roma"},
    # Tracking
    {"slug": "tracking_pixel_url",   "label": "Pixel tracking (URL)", "example": "https://..."},
]


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class CreateTemplateInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=500)
    html: str = Field(min_length=10)
    plain_text: str | None = Field(default=None)


class UpdateTemplateInput(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    subject: str | None = Field(default=None, min_length=1, max_length=500)
    html: str | None = Field(default=None, min_length=10)
    plain_text: str | None = Field(default=None)


class ValidateTemplateInput(BaseModel):
    html: str = Field(min_length=10)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_variables(html: str) -> list[str]:
    """Return sorted list of unique Jinja2 variable names referenced in html."""
    # Match {{ var_name }} — ignore attribute access (roi.foo), filters, etc.
    found = re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[|}]", html)
    return sorted(set(found))


def _check_required_vars(html: str) -> list[str]:
    """Return the REQUIRED_VARS that are missing from html."""
    found = set(_extract_variables(html))
    return sorted(REQUIRED_VARS - found)


def _template_belongs_to_tenant(sb: Any, template_id: str, tenant_id: str) -> bool:
    res = (
        sb.table("email_templates")
        .select("id")
        .eq("id", template_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    return bool(res.data)


# ---------------------------------------------------------------------------
# Sample context for preview rendering
# ---------------------------------------------------------------------------

_PREVIEW_CONTEXT: dict[str, Any] = {
    "greeting_name":        "Mario Rossi",
    "business_name":        "Studio Rossi Amministrazioni",
    "hq_address":           "Via Roma 42",
    "hq_cap":               "80100",
    "hq_city":              "Napoli",
    "hq_province":          "NA",
    "phone":                "+39 081 123 4567",
    "recipient_email":      "mario.rossi@esempio.it",
    "sender_first_name":    "Alfonso",
    "tenant_name":          "SolarTech",
    "brand_logo_url":       "",
    "unsubscribe_url":      "https://solarld.app/optout/preview",
    "tenant_legal_name":    "SolarTech S.r.l.",
    "tenant_vat_number":    "IT12345678901",
    "tenant_legal_address": "Via Milano 10, 20100 Milano MI",
    "tracking_pixel_url":   "https://solarld.app/track/preview",
}


def _render_preview(html: str, subject: str) -> dict[str, str]:
    """Render template with sample context via Jinja2. Best-effort."""
    try:
        from jinja2 import Environment, Undefined
        env = Environment(autoescape=False, undefined=Undefined)
        rendered_html = env.from_string(html).render(**_PREVIEW_CONTEXT)
        rendered_subject = env.from_string(subject).render(**_PREVIEW_CONTEXT)
        return {"html": rendered_html, "subject": rendered_subject}
    except Exception as exc:  # noqa: BLE001
        log.warning("email_template.preview_render_failed", err=str(exc)[:200])
        return {"html": html, "subject": subject}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_templates(ctx: CurrentUser) -> dict[str, Any]:
    """List all custom email templates for the current tenant."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("email_templates")
        .select("id, name, subject, variables_used, created_at, updated_at")
        .eq("tenant_id", tenant_id)
        .order("updated_at", desc=True)
        .execute()
    )
    return {"items": res.data or [], "count": len(res.data or [])}


@router.get("/variables")
async def list_variables(ctx: CurrentUser) -> dict[str, Any]:
    """Return documented variables with labels and examples for the UI."""
    require_tenant(ctx)
    return {"variables": ALL_VARS, "required": sorted(REQUIRED_VARS)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_template(body: CreateTemplateInput, ctx: CurrentUser) -> dict[str, Any]:
    """Create a new email template. Validates GDPR required variables."""
    tenant_id = require_tenant(ctx)

    missing = _check_required_vars(body.html)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "missing_required_variables",
                "missing": missing,
                "message": (
                    f"Il template deve contenere le variabili GDPR obbligatorie: "
                    f"{', '.join(f'{{{{ {v} }}}}' for v in missing)}"
                ),
            },
        )

    vars_used = _extract_variables(body.html)
    sb = get_service_client()
    res = sb.table("email_templates").insert({
        "tenant_id": tenant_id,
        "name": body.name,
        "subject": body.subject,
        "html": body.html,
        "plain_text": body.plain_text,
        "variables_used": vars_used,
    }).execute()
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="insert_failed",
        )
    return res.data[0]


@router.get("/{template_id}")
async def get_template(template_id: str, ctx: CurrentUser) -> dict[str, Any]:
    """Return a single email template (full HTML included)."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("email_templates")
        .select("*")
        .eq("id", template_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template_not_found")
    return res.data[0]


@router.patch("/{template_id}")
async def update_template(
    template_id: str, body: UpdateTemplateInput, ctx: CurrentUser
) -> dict[str, Any]:
    """Partial update — only provided fields are changed."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    if not _template_belongs_to_tenant(sb, template_id, tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template_not_found")

    patch: dict[str, Any] = {}
    if body.name is not None:
        patch["name"] = body.name
    if body.subject is not None:
        patch["subject"] = body.subject
    if body.html is not None:
        missing = _check_required_vars(body.html)
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "missing_required_variables",
                    "missing": missing,
                    "message": (
                        f"Variabili GDPR obbligatorie mancanti: "
                        f"{', '.join(f'{{{{ {v} }}}}' for v in missing)}"
                    ),
                },
            )
        patch["html"] = body.html
        patch["variables_used"] = _extract_variables(body.html)
    if body.plain_text is not None:
        patch["plain_text"] = body.plain_text

    if not patch:
        # Nothing to update — return the current row.
        res = (
            sb.table("email_templates").select("*")
            .eq("id", template_id).limit(1).execute()
        )
        return res.data[0]

    patch["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = (
        sb.table("email_templates")
        .update(patch)
        .eq("id", template_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="update_failed"
        )
    return res.data[0]


@router.delete("/{template_id}", status_code=status.HTTP_200_OK)
async def delete_template(template_id: str, ctx: CurrentUser) -> dict[str, Any]:
    """Hard-delete a template. Associated prospect_lists lose the FK (SET NULL)."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("email_templates")
        .delete()
        .eq("id", template_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template_not_found")
    return {"deleted": True, "id": template_id}


@router.post("/{template_id}/preview")
async def preview_template(template_id: str, ctx: CurrentUser) -> dict[str, Any]:
    """Render the stored template with sample data. Returns rendered HTML + subject."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("email_templates")
        .select("html, subject")
        .eq("id", template_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template_not_found")
    row = res.data[0]
    return _render_preview(row["html"], row["subject"])


@router.post("/validate")
async def validate_template(body: ValidateTemplateInput, ctx: CurrentUser) -> dict[str, Any]:
    """Validate HTML without saving — returns missing variables list."""
    require_tenant(ctx)
    missing = _check_required_vars(body.html)
    vars_found = _extract_variables(body.html)
    return {
        "valid": len(missing) == 0,
        "missing_required": missing,
        "variables_found": vars_found,
    }


# ---------------------------------------------------------------------------
# Helper used by OutreachAgent + prospect_list_outreach
# ---------------------------------------------------------------------------


def get_template_for_list(
    sb: Any, *, list_id: str, tenant_id: str
) -> dict[str, Any] | None:
    """Return the email_template row linked to a prospect_list, or None."""
    res = (
        sb.table("prospect_lists")
        .select("email_template_id")
        .eq("id", list_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    tpl_id = (res.data[0] or {}).get("email_template_id")
    if not tpl_id:
        return None
    tpl_res = (
        sb.table("email_templates")
        .select("id, name, subject, html, plain_text")
        .eq("id", tpl_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    return (tpl_res.data or [None])[0]


def render_template_for_lead(
    template: dict[str, Any],
    *,
    lead: dict[str, Any],
    subject_row: dict[str, Any],
    roof: dict[str, Any],
    tenant_row: dict[str, Any],
    optout_url: str,
    tracking_pixel_url: str | None = None,
) -> dict[str, str]:
    """Render a stored template with real lead data.

    Returns {"html": ..., "subject": ..., "text": ...}.
    Falls back gracefully on Jinja2 render errors.
    """
    from jinja2 import Environment, Undefined

    # Build context from lead data.
    place_blob = (subject_row.get("raw_data") or {}).get("enrichment_places") or {}
    scraped = {}
    # Try contact extraction
    contact = {}

    # Resolve address pieces — prefer scan_candidate enrichment data via raw_data,
    # fall back to subject fields.
    hq_address = (
        subject_row.get("sede_operativa_address")
        or place_blob.get("formatted_address")
        or ""
    )

    # Parse CAP / city / province from the address string best-effort.
    # The subject row may already have these (sede_operativa_*).
    # For now expose the full address and let the template author decide.

    context: dict[str, Any] = {
        "greeting_name":        subject_row.get("decision_maker_name") or subject_row.get("business_name") or "",
        "business_name":        subject_row.get("business_name") or "",
        "hq_address":           hq_address,
        "hq_cap":               "",
        "hq_city":              "",
        "hq_province":          "",
        "phone":                subject_row.get("decision_maker_phone") or "",
        "recipient_email":      subject_row.get("decision_maker_email") or "",
        "sender_first_name":    (tenant_row.get("email_from_name") or tenant_row.get("business_name") or "").split()[0],
        "tenant_name":          tenant_row.get("business_name") or "",
        "brand_logo_url":       tenant_row.get("brand_logo_url") or "",
        "unsubscribe_url":      optout_url,
        "tenant_legal_name":    tenant_row.get("legal_name") or tenant_row.get("business_name") or "",
        "tenant_vat_number":    tenant_row.get("vat_number") or "",
        "tenant_legal_address": tenant_row.get("legal_address") or "",
        "tracking_pixel_url":   tracking_pixel_url or "",
    }

    env = Environment(autoescape=False, undefined=Undefined)

    try:
        rendered_html = env.from_string(template["html"]).render(**context)
    except Exception as exc:  # noqa: BLE001
        log.warning("email_template.render_html_failed", err=str(exc)[:200])
        rendered_html = template["html"]

    try:
        rendered_subject = env.from_string(template["subject"]).render(**context)
    except Exception as exc:  # noqa: BLE001
        log.warning("email_template.render_subject_failed", err=str(exc)[:200])
        rendered_subject = template["subject"]

    plain = template.get("plain_text") or ""
    if plain:
        try:
            plain = env.from_string(plain).render(**context)
        except Exception:  # noqa: BLE001
            pass
    else:
        # Minimal plain-text from the unsubscribe URL.
        plain = (
            f"{context['tenant_name']}\n\n"
            f"Per non ricevere ulteriori comunicazioni: {optout_url}\n"
            f"{context['tenant_legal_name']} — P.IVA {context['tenant_vat_number']}\n"
            f"{context['tenant_legal_address']}\n"
        )

    return {"html": rendered_html, "subject": rendered_subject, "text": plain}
