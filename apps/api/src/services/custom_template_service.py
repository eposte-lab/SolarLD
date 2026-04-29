"""Custom tenant email template service (Sprint 9 Fase C.2).

Tenants can upload a Jinja2-compatible HTML email template from
``/settings/email-template`` in the dashboard.  This service handles:

  1. Validation — parse Jinja2 syntax and verify all GDPR-mandatory
     template variables are present.

  2. Sanitization — strip unsafe tags/attributes via ``bleach`` (defence
     in depth; a tenant might paste a downloaded HTML with inline
     <script> or event-handler attributes).

  3. Storage — write/read the template file from Supabase Storage bucket
     ``branding`` at path ``{tenant_id}/email_template.html.j2``.

  4. Rendering — load from Storage, render via Jinja2 + premailer
     CSS-inlining, identical pipeline to the built-in templates.

All functions are pure or depend only on their explicit arguments so they
are easy to unit-test without a Supabase fixture.

Usage in ``email_template_service.render_outreach_email()``:
  - If tenant has ``custom_email_template_active=True``, call
    ``render_custom_template(supabase, tenant_id, context_dict)``.
  - On any error log + fall through to premium / legacy stem.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import bleach
from jinja2 import Environment, TemplateSyntaxError, Undefined, meta

from ..core.logging import get_logger

log = get_logger(__name__)

# ── GDPR-mandatory Jinja2 variables ──────────────────────────────────
# The tenant's template MUST reference all of these variables; if any
# are missing the upload is rejected with a clear error message.

REQUIRED_VARIABLES: frozenset[str] = frozenset({
    "unsubscribe_url",       # RFC 8058 one-click opt-out link
    "tracking_pixel_url",    # 1×1 pixel for open tracking
    "tenant_legal_name",     # e.g. "SolarTech S.r.l."
    "tenant_vat_number",     # P.IVA per GDPR footer
    "tenant_legal_address",  # Registered address per GDPR footer
})

# Optional variables documented in the upload UI — tenants can use
# any subset; the renderer passes all of them as Jinja context.
OPTIONAL_VARIABLES: frozenset[str] = frozenset({
    "copy_subject",
    "copy_opening_line",
    "copy_proposition_line",
    "cta_primary_label",
    "greeting_name",
    "lead_url",
    "tenant_name",
    "business_name",
    "ateco_description",
    "hero_gif_url",
    "hero_image_url",
    "brand_primary_color",
    "brand_color_accent",
    "brand_logo_url",
    "roi",
    "sequence_step",
    "sender_first_name",
    "hq_province",
    "recipient_email",
    "video_landing_url",
    "similar_province",
})

# bleach allowlist — generous for email HTML but strips anything risky.
_ALLOWED_TAGS: list[str] = [
    "html", "head", "body", "meta", "title", "style", "link",
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "a", "img",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th",
    "span", "div", "section", "header", "footer", "main",
    "strong", "b", "em", "i", "u", "s", "small",
    "ul", "ol", "li",
    "pre", "code",
    "center",    # legacy email layout
    "font",      # legacy email layout
    "blockquote",
]
_ALLOWED_ATTRIBUTES: dict[str, list[str]] = {
    "*": ["class", "id", "style"],
    "a": ["href", "target", "rel", "title"],
    "img": ["src", "alt", "width", "height", "border"],
    "td": ["colspan", "rowspan", "align", "valign", "width", "height",
           "bgcolor", "background"],
    "th": ["colspan", "rowspan", "align", "valign", "width", "height",
           "bgcolor"],
    "tr": ["align", "valign", "bgcolor"],
    "table": ["align", "width", "border", "cellpadding", "cellspacing",
              "bgcolor", "background"],
    "div": ["align"],
    "center": [],
    "font": ["size", "color", "face"],
    "meta": ["charset", "name", "content", "http-equiv"],
    "link": ["rel", "href", "type"],
}
_MAX_SIZE_BYTES: int = 200 * 1024   # 200 KB post-sanitize

# Path in Supabase Storage branding bucket.
_STORAGE_BUCKET = "branding"


@dataclass
class ValidationResult:
    valid: bool
    missing_variables: list[str] = field(default_factory=list)
    syntax_error: str | None = None
    size_error: str | None = None
    sanitized_html: str | None = None   # only set when valid=True


# ── Public API ────────────────────────────────────────────────────────


def validate_custom_html(html: str) -> ValidationResult:
    """Validate and sanitize a tenant-supplied HTML template.

    Steps:
      1. Parse Jinja2 syntax — catch ``TemplateSyntaxError``.
      2. Extract all ``{{ variable }}`` references using
         ``jinja2.meta.find_undeclared_variables``.
      3. Diff against ``REQUIRED_VARIABLES`` — missing ones → fail.
      4. Sanitize via ``bleach`` (strips <script>, event handlers,
         ``javascript:`` hrefs, etc.).
      5. Check post-sanitize byte size ≤ 200 KB.

    Returns ``ValidationResult`` with either a populated
    ``sanitized_html`` (on success) or error details (on failure).
    """
    # Step 1 — Jinja2 syntax check.
    env = _jinja_env()
    try:
        ast = env.parse(html)
    except TemplateSyntaxError as exc:
        # Surface the line number + Jinja2's own message — the tenant
        # is editing template syntax, so the framework's pointer at the
        # bad token is what they need. Drop the framework name
        # ("Jinja2") from the user-facing copy; ops know it from logs.
        return ValidationResult(
            valid=False,
            syntax_error=f"Errore di sintassi del template alla riga {exc.lineno}: {exc.message}",
        )

    # Step 2 — extract referenced variables.
    referenced = meta.find_undeclared_variables(ast)

    # Step 3 — check required variables.
    missing = sorted(REQUIRED_VARIABLES - referenced)
    if missing:
        return ValidationResult(
            valid=False,
            missing_variables=missing,
        )

    # Step 4 — sanitize.
    sanitized = bleach.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        strip=True,
        strip_comments=False,   # keep <!-- comments --> (used for MSO conditional code)
    )

    # Step 5 — size check.
    size_bytes = len(sanitized.encode("utf-8"))
    if size_bytes > _MAX_SIZE_BYTES:
        return ValidationResult(
            valid=False,
            size_error=(
                f"Template troppo grande: {size_bytes // 1024} KB "
                f"(massimo {_MAX_SIZE_BYTES // 1024} KB)."
            ),
        )

    return ValidationResult(valid=True, sanitized_html=sanitized)


def _storage_path(tenant_id: str) -> str:
    return str(PurePosixPath(tenant_id) / "email_template.html.j2")


async def save_custom_template(supabase: Any, tenant_id: str, html: str) -> str:
    """Validate, sanitize and upload the template to Supabase Storage.

    Returns the storage path on success; raises ``ValueError`` on
    validation failure (with a human-readable message).
    """
    result = validate_custom_html(html)
    if not result.valid:
        if result.syntax_error:
            raise ValueError(result.syntax_error)
        if result.missing_variables:
            missing_str = ", ".join(result.missing_variables)
            raise ValueError(
                f"Variabili GDPR obbligatorie mancanti: {missing_str}. "
                "Aggiungi i segnaposto {{ nome_variabile }} nel template."
            )
        if result.size_error:
            raise ValueError(result.size_error)
        raise ValueError("Template non valido.")

    path = _storage_path(tenant_id)
    content_bytes = (result.sanitized_html or "").encode("utf-8")

    # Upload to Supabase Storage (service-role, bypasses RLS).
    upload_resp = await supabase.storage.from_(_STORAGE_BUCKET).upload(
        path,
        io.BytesIO(content_bytes),
        file_options={
            "content-type": "text/html",
            "upsert": "true",
        },
    )
    if hasattr(upload_resp, "error") and upload_resp.error:
        raise RuntimeError(f"Storage upload failed: {upload_resp.error.message}")

    # Update tenant metadata.
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    await supabase.table("tenants").update({
        "custom_email_template_path": path,
        "custom_email_template_uploaded_at": now_iso,
        "custom_email_template_active": True,
    }).eq("id", tenant_id).execute()

    log.info(
        "custom_template.saved",
        tenant_id=tenant_id,
        path=path,
        size_bytes=len(content_bytes),
    )
    return path


async def deactivate_custom_template(supabase: Any, tenant_id: str) -> None:
    """Set custom_email_template_active=False without deleting the file."""
    await supabase.table("tenants").update({
        "custom_email_template_active": False,
    }).eq("id", tenant_id).execute()
    log.info("custom_template.deactivated", tenant_id=tenant_id)


async def render_custom_template(
    supabase: Any,
    tenant_id: str,
    template_path: str,
    context: dict[str, Any],
) -> str:
    """Download the template from Storage, render via Jinja2, inline CSS.

    Returns the final HTML string.  Raises on any failure (let the caller
    fall back to the premium/legacy template).

    Args:
        supabase: Service-role Supabase client.
        tenant_id: Used only for logging.
        template_path: Storage path, e.g. ``"{tenant_id}/email_template.html.j2"``.
        context: Full Jinja2 context dict (same as passed to built-in templates).

    Returns:
        CSS-inlined HTML string.
    """
    from premailer import transform as premailer_transform

    # Download from Storage.
    dl = await supabase.storage.from_(_STORAGE_BUCKET).download(template_path)
    if isinstance(dl, (bytes, bytearray)):
        raw_html = dl.decode("utf-8")
    elif hasattr(dl, "read"):
        raw_html = dl.read().decode("utf-8")
    else:
        # Supabase Python SDK returns the bytes directly.
        raise RuntimeError(f"Unexpected download response type: {type(dl)}")

    # Render via Jinja2.
    env = _jinja_env()
    tmpl = env.from_string(raw_html)
    rendered = tmpl.render(**context)

    # Inline CSS.
    inlined = premailer_transform(
        rendered,
        keep_style_tags=True,
        remove_classes=False,
        disable_validation=True,
    )

    log.info(
        "custom_template.rendered",
        tenant_id=tenant_id,
        template_path=template_path,
        output_bytes=len(inlined),
    )
    return inlined


async def get_preview_html(
    supabase: Any,
    tenant_id: str,
    template_path: str,
) -> str:
    """Render the custom template with sample data for preview."""
    sample_context = {
        "unsubscribe_url": "https://solarld.app/optout/preview",
        "tracking_pixel_url": "https://solarld.app/track/preview",
        "tenant_legal_name": "SolarTech S.r.l. (preview)",
        "tenant_vat_number": "IT12345678901",
        "tenant_legal_address": "Via Roma 1, 00100 Roma RM",
        "tenant_name": "SolarTech",
        "greeting_name": "Mario Rossi",
        "lead_url": "https://solarld.app/l/preview",
        "optout_url": "https://solarld.app/optout/preview",
        "brand_primary_color": "#0F766E",
        "brand_color_accent": "#F4A300",
        "brand_logo_url": None,
        "business_name": "Rossi Costruzioni Srl",
        "ateco_description": "Costruzione di edifici residenziali e non residenziali",
        "copy_subject": "Oggetto email di esempio",
        "copy_opening_line": "Ho analizzato la vostra sede e i risultati sono interessanti.",
        "copy_proposition_line": "Vi proponiamo un sopralluogo gratuito senza impegno.",
        "cta_primary_label": "Scopri l'analisi completa",
        "hero_gif_url": None,
        "hero_image_url": None,
        "roi": {
            "estimated_kwp": 12.0,
            "yearly_savings_eur": 2100,
            "payback_years": 7,
            "co2_tonnes_25_years": 45,
            "total_savings_25_years": 52500,
        },
        "sequence_step": 1,
        "sender_first_name": "Alfonso",
        "hq_province": "Napoli",
        "recipient_email": "destinatario@esempio.it",
        "video_landing_url": None,
        "similar_province": "Napoli",
    }
    return await render_custom_template(supabase, tenant_id, template_path, sample_context)


# ── Internal helpers ──────────────────────────────────────────────────

_jinja_env_instance: Environment | None = None


def _jinja_env() -> Environment:
    """Return a shared Jinja2 Environment for validation + rendering.

    Uses ``Undefined`` (not ``StrictUndefined``) so that tenant templates
    that reference optional variables (like ``roi.yearly_savings_eur``)
    don't blow up at parse/render time if those fields happen to be null
    in a particular send context.  The required-variable check in
    ``validate_custom_html`` handles the GDPR-mandatory ones explicitly.
    """
    global _jinja_env_instance
    if _jinja_env_instance is None:
        from jinja2 import Environment as Env
        _jinja_env_instance = Env(
            autoescape=False,   # templates already sanitized via bleach
            undefined=Undefined,
        )
    return _jinja_env_instance
