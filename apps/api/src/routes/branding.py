"""Branding & email domain management endpoints.

Routes:
  GET  /v1/branding/email-preview          Render sample email HTML with current (or preview) brand
  POST /v1/branding/domain/setup           Add domain to Resend, persist DNS records
  GET  /v1/branding/domain/status          Poll Resend for verification status + DNS records
  POST /v1/branding/generate-variants      Claude-powered subject+preheader variant generation (B.13)
  POST /v1/branding/regenerate-email       Claude-powered full email content + style generation (B.14)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..core.config import settings
from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..services.claude_service import complete as claude_complete
from ..services.email_template_service import OutreachContext, render_outreach_email

log = get_logger(__name__)
router = APIRouter()

_RESEND_API = "https://api.resend.com"
_DEFAULT_BRAND_COLOR = "#0F766E"


# ============================================================
# Data models
# ============================================================


class DnsRecord(BaseModel):
    type: str           # MX | TXT | CNAME
    name: str           # hostname
    value: str          # record value
    priority: int | None = None
    ttl: int | None = None
    status: str = "not_started"   # not_started | pending | verified | failed (per-record)


class DomainSetupRequest(BaseModel):
    domain: str = Field(min_length=4, max_length=253)
    email_from_name: str | None = Field(default=None, max_length=100)


class DomainStatusResponse(BaseModel):
    domain_id: str
    domain: str
    status: str          # not_started | pending | verified | failed
    dns_records: list[DnsRecord]
    created_at: str | None = None


class EmailVariant(BaseModel):
    subject: str
    preheader: str
    body_preview: str    # 2-3 sentence plain-text summary — shown in the preview card
    rationale: str       # 1 sentence why this variant may win


class GenerateVariantsRequest(BaseModel):
    subject_type: Literal["b2b", "b2c"] = "b2c"
    tone: Literal["professional", "urgent", "friendly", "roi_focused"] = "professional"
    count: int = Field(default=3, ge=1, le=5)
    context_hint: str | None = Field(
        default=None,
        max_length=300,
        description="Optional free-text steering prompt from the operator",
    )


class GenerateVariantsResponse(BaseModel):
    variants: list[EmailVariant]
    subject_type: str
    tone: str


# ============================================================
# A. Email preview
# ============================================================


@router.get("/email-preview", response_class=HTMLResponse)
async def get_email_preview(
    ctx: CurrentUser,
    template: Annotated[Literal["b2b", "b2c"], Query()] = "b2c",
    step: Annotated[int, Query(ge=1, le=3)] = 1,
    # Optional query-param overrides so the branding editor can preview
    # live changes before saving them.
    color: Annotated[str | None, Query(max_length=20)] = None,
    tenant_name_override: Annotated[str | None, Query(max_length=120, alias="tenant_name")] = None,
    logo_url: Annotated[str | None, Query(max_length=500)] = None,
    style: Annotated[Literal["classic", "bold", "minimal"], Query()] = "classic",
) -> HTMLResponse:
    """Render a sample outreach email with the tenant's current brand.

    Returns raw HTML ready for embedding in an ``<iframe srcdoc="…">``.
    Accepts optional query overrides so the branding editor can show a
    live preview without persisting the change first.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    t_res = (
        sb.table("tenants")
        .select("business_name, brand_primary_color, brand_logo_url, settings")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    if not t_res.data:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant = t_res.data[0]
    tenant_settings: dict[str, Any] = dict(tenant.get("settings") or {})
    email_copy: dict[str, Any] = dict(tenant_settings.get("email_copy_overrides") or {})

    brand_color = (color or tenant.get("brand_primary_color") or _DEFAULT_BRAND_COLOR).strip()
    # Ensure hex prefix
    if brand_color and not brand_color.startswith("#"):
        brand_color = f"#{brand_color}"

    name = (tenant_name_override or tenant.get("business_name") or "La tua azienda").strip()
    effective_logo = logo_url or tenant.get("brand_logo_url")

    sample_roi = {
        "estimated_kwp": 12.0,
        "yearly_savings_eur": 2100,
        "payback_years": 7,
        "co2_tonnes_25_years": 45,
    }
    ctx_tmpl = OutreachContext(
        tenant_name=name,
        brand_primary_color=brand_color,
        brand_logo_url=effective_logo,
        greeting_name="Mario Rossi" if template == "b2c" else "Responsabile acquisti",
        lead_url="https://solarlead.it/l/preview",
        optout_url="https://solarlead.it/optout/preview",
        subject_template=f"{name} — anteprima email",
        subject_type=template,
        roi=sample_roi,
        hero_image_url=None,
        hero_gif_url=None,
        personalized_opener=(
            "Stavo analizzando le opportunità di risparmio energetico nella "
            "sua zona e ho trovato qualcosa di interessante."
        ),
        business_name="Rossi Costruzioni Srl" if template == "b2b" else None,
        sequence_step=step,
        template_style=style,
        headline=email_copy.get("headline"),
        main_copy_1=email_copy.get("main_copy_1"),
        main_copy_2=email_copy.get("main_copy_2"),
        cta_text=email_copy.get("cta_text"),
    )

    try:
        rendered = render_outreach_email(ctx_tmpl)
    except Exception as exc:
        log.warning("branding.preview_render_failed", err=str(exc))
        raise HTTPException(status_code=500, detail="Template rendering failed") from exc

    return HTMLResponse(content=rendered.html, status_code=200)


# ============================================================
# B. Domain setup & verification
# ============================================================


@router.post("/domain/setup", response_model=DomainStatusResponse)
async def setup_domain(
    ctx: CurrentUser, body: DomainSetupRequest
) -> DomainStatusResponse:
    """Add a custom sending domain to Resend and persist its DNS records.

    1. If the tenant already has a ``resend_domain_id`` for the *same*
       domain → re-fetch current status instead of creating a duplicate.
    2. Otherwise → POST /domains, store ID, update ``email_from_domain``.
    3. If Resend returns 422 (domain already registered under our account)
       → list domains, find by name, proceed as (2).
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    t_res = (
        sb.table("tenants")
        .select("email_from_domain, settings")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    if not t_res.data:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant = t_res.data[0]
    current_settings: dict[str, Any] = dict(tenant.get("settings") or {})
    existing_id: str | None = current_settings.get("resend_domain_id")
    existing_domain: str | None = tenant.get("email_from_domain")

    domain = body.domain.lower().strip()

    # Same domain already configured → refresh status from Resend.
    # If Resend no longer knows this ID (deleted via dashboard) we clear
    # the stale resend_domain_id and fall through to re-register.
    if existing_id and existing_domain == domain:
        try:
            return await _fetch_domain_status(existing_id)
        except HTTPException as exc:
            log.warning(
                "branding.stale_domain_id",
                tenant_id=tenant_id,
                domain=domain,
                existing_id=existing_id,
                status_code=exc.status_code,
            )
            # Wipe the stale ID so the fall-through path can re-register cleanly.
            stale_settings = {k: v for k, v in current_settings.items() if k != "resend_domain_id"}
            sb.table("tenants").update({"settings": stale_settings}).eq("id", tenant_id).execute()
            existing_id = None

    if not settings.resend_api_key:
        raise HTTPException(status_code=503, detail="Resend API key not configured")

    # Pre-flight: look up the domain in our Resend account first. This is
    # both cheaper (no needless POST) and handles the "orphan from a
    # previous failed setup" case deterministically — the user never has
    # to touch the Resend dashboard.
    pre_existing = await _find_domain_by_name(domain)
    domain_id: str = ""

    if pre_existing:
        domain_id = str(pre_existing.get("id") or "")
        log.info(
            "branding.domain_linked_from_resend_list",
            tenant_id=tenant_id,
            domain=domain,
            domain_id=domain_id,
        )

    if not domain_id:
        # Not in our Resend account — create it fresh.
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{_RESEND_API}/domains",
                headers=_resend_headers(),
                json={"name": domain},
            )

        if resp.status_code == 422:
            # Two known 422 causes from Resend:
            #   a) Invalid format (bare TLD, internal hostname, ...)
            #   b) Race condition — domain created by a concurrent call between
            #      our pre-flight list and this POST. Re-query to recover.
            resend_err: dict[str, Any] = {}
            try:
                resend_err = resp.json()
            except Exception:  # noqa: BLE001
                pass
            resend_msg = str(resend_err.get("message") or "").lower()
            log.warning(
                "branding.domain_resend_422",
                domain=domain,
                resend_body=resp.text[:400],
            )

            if any(kw in resend_msg for kw in ("invalid", "format", "not valid", "reserved")):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Il dominio '{domain}' non è valido o non è accettato da Resend. "
                        "Verifica che sia un dominio DNS pubblico reale (es. tuodominio.it)."
                    ),
                )

            retry = await _find_domain_by_name(domain)
            if not retry:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Resend ha rifiutato il dominio '{domain}' (422) ma non è "
                        "visibile nella lista dei domini. Riprova tra qualche secondo "
                        "o usa 'Disconnetti dominio' per ripartire da zero."
                    ),
                )
            domain_id = str(retry["id"])
            log.info("branding.domain_recovered_race", domain=domain, domain_id=domain_id)
        elif resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=502,
                detail=f"Resend domain creation failed: {resp.status_code} — {resp.text[:200]}",
            )
        else:
            created = resp.json() or {}
            domain_id = str(created.get("id") or created.get("domain_id") or "")

    if not domain_id:
        raise HTTPException(status_code=502, detail="Resend returned no domain id")

    # Persist
    updated_settings = {**current_settings, "resend_domain_id": domain_id}
    update_payload: dict[str, Any] = {
        "email_from_domain": domain,
        "settings": updated_settings,
    }
    if body.email_from_name:
        update_payload["email_from_name"] = body.email_from_name

    sb.table("tenants").update(update_payload).eq("id", tenant_id).execute()
    log.info("branding.domain_setup", tenant_id=tenant_id, domain=domain, domain_id=domain_id)

    # The domain is registered and our DB is up to date — even if the
    # follow-up status fetch fails (Resend payload shape drift, 5xx,
    # transient timeout), we must return 200 so the UI shows the domain
    # as connected. The user can click "Ricontrolla" to populate DNS
    # records; otherwise they see a generic "Failed to fetch" and think
    # nothing happened (when in fact the domain IS live on Resend).
    try:
        return await _fetch_domain_status(domain_id)
    except HTTPException as exc:
        log.warning(
            "branding.domain_setup_status_fetch_failed",
            tenant_id=tenant_id,
            domain=domain,
            domain_id=domain_id,
            status_code=exc.status_code,
            detail=str(exc.detail)[:200],
        )
        return DomainStatusResponse(
            domain_id=domain_id,
            domain=domain,
            status="pending",
            dns_records=[],
            created_at=None,
        )


@router.get("/domain/status", response_model=DomainStatusResponse)
async def get_domain_status(ctx: CurrentUser) -> DomainStatusResponse:
    """Poll Resend for current domain verification status.

    Automatically stamps ``email_from_domain_verified_at`` on the tenant
    row the first time Resend reports ``status=verified``.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    t_res = (
        sb.table("tenants")
        .select("email_from_domain_verified_at, settings")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    if not t_res.data:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant = t_res.data[0]
    current_settings: dict[str, Any] = dict(tenant.get("settings") or {})
    domain_id: str | None = current_settings.get("resend_domain_id")

    if not domain_id:
        raise HTTPException(status_code=404, detail="No domain configured for this tenant")

    # trigger_verify=True fires POST /domains/{id}/verify on Resend so the
    # DNS check happens immediately instead of waiting for their background
    # polling interval (which can be up to 24 h on free plans).
    result = await _fetch_domain_status(domain_id, trigger_verify=True)

    # Stamp verification timestamp on first success
    if result.status == "verified" and not tenant.get("email_from_domain_verified_at"):
        sb.table("tenants").update(
            {"email_from_domain_verified_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", tenant_id).execute()
        log.info("branding.domain_verified", tenant_id=tenant_id, domain=result.domain)

    return result


@router.delete("/domain")
async def disconnect_domain(ctx: CurrentUser) -> dict[str, str]:
    """Fully disconnect the tenant's sending domain.

    Removes the domain from Resend (so it's no longer billed or listed)
    and wipes every related column/jsonb key on the tenant row. Gives
    the installer a clean slate they can reach entirely from the
    dashboard — no Resend console access required.

    Idempotent: if Resend already doesn't know the domain (404) we still
    wipe the local state and return 204. If the tenant never had a
    domain configured we also return 204.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    t_res = (
        sb.table("tenants")
        .select("email_from_domain, settings")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    if not t_res.data:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant = t_res.data[0]
    current_settings: dict[str, Any] = dict(tenant.get("settings") or {})
    domain_id: str | None = current_settings.get("resend_domain_id")
    domain_name: str | None = tenant.get("email_from_domain")

    # 1) Delete from Resend.
    # Prefer the known domain_id. If it's missing but we know the name,
    # look it up — covers the "DB state lost but Resend still has it" case.
    if not domain_id and domain_name:
        found = await _find_domain_by_name(domain_name)
        if found:
            domain_id = str(found.get("id") or "")

    if domain_id and settings.resend_api_key:
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.delete(
                    f"{_RESEND_API}/domains/{domain_id}",
                    headers=_resend_headers(),
                )
            if resp.status_code not in (200, 202, 204, 404):
                log.warning(
                    "branding.domain_delete_resend_non2xx",
                    tenant_id=tenant_id,
                    status=resp.status_code,
                    body=resp.text[:300],
                )
            else:
                log.info(
                    "branding.domain_deleted_from_resend",
                    tenant_id=tenant_id,
                    domain_id=domain_id,
                )
        except Exception as exc:  # noqa: BLE001
            # Don't block the local cleanup on a flaky Resend call — the
            # user's intent is "reset my state". Log it and continue.
            log.warning(
                "branding.domain_delete_resend_failed",
                tenant_id=tenant_id,
                err=str(exc),
            )

    # 2) Wipe local state — column + jsonb key together.
    cleaned = {k: v for k, v in current_settings.items() if k != "resend_domain_id"}
    sb.table("tenants").update(
        {
            "email_from_domain": None,
            "email_from_domain_verified_at": None,
            "settings": cleaned,
        }
    ).eq("id", tenant_id).execute()

    log.info("branding.domain_disconnected", tenant_id=tenant_id, domain=domain_name)
    return {"status": "disconnected"}


# ============================================================
# C. AI variant generation (B.13)
# ============================================================


@router.post("/generate-variants", response_model=GenerateVariantsResponse)
async def generate_email_variants(
    ctx: CurrentUser, body: GenerateVariantsRequest
) -> GenerateVariantsResponse:
    """Generate N email subject + preheader + body variants via Claude.

    Output is designed to be pasted directly into the A/B experiment
    creation form (variant_a_subject / variant_b_subject) or used as
    inspiration for template copy updates.

    No caching: results are cheap, intentionally non-deterministic, and
    should reflect the tenant's latest context.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    t_res = (
        sb.table("tenants")
        .select("business_name")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    if not t_res.data:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant_name = (t_res.data[0].get("business_name") or "la tua azienda").strip()

    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="AI not configured")

    tone_desc = {
        "professional": "professionale e autorevole",
        "urgent": "urgente, con senso di scarsità (stagione estiva, incentivi in scadenza)",
        "friendly": "cordiale e vicino, quasi da vicino di casa",
        "roi_focused": "focalizzato sui numeri: risparmio annuo in euro e anni di rientro",
    }.get(body.tone, "professionale")

    segment_desc = (
        "titolari e responsabili acquisti di aziende"
        if body.subject_type == "b2b"
        else "proprietari di abitazioni private"
    )
    hint_frag = (
        f"\nIstruzione extra dall'operatore: «{body.context_hint}»"
        if body.context_hint
        else ""
    )

    prompt = f"""Sei un esperto copywriter email per il settore energia solare in Italia.
Genera esattamente {body.count} varianti di email outreach per "{tenant_name}", rivolte a {segment_desc}.
Tono target: {tone_desc}.{hint_frag}

Ogni variante deve avere quattro campi:
1. subject: oggetto email (max 60 caratteri, niente emoji, niente punti esclamativi multipli)
2. preheader: testo preheader (max 90 caratteri, aggiunge contesto al subject senza ripeterlo)
3. body_preview: 2-3 frasi che riassumono il corpo dell'email (testo semplice, niente HTML)
4. rationale: 1 frase su perché questa variante potrebbe aumentare il tasso di apertura

Vincoli:
- Angolo diverso in ogni variante (risparmio bolletta, autonomia energetica, impatto CO₂, rendering 3D del tetto, urgenza stagionale…)
- Non citare mai prezzi fissi né "gratis" / "GRATUITO"
- Rimanere sempre veritieri e coerenti con una proposta commerciale solare B2B/B2C italiana

Rispondi SOLO con JSON valido, senza markdown né ```json```, in questo formato esatto:
{{"variants": [{{"subject": "…", "preheader": "…", "body_preview": "…", "rationale": "…"}}]}}"""

    try:
        raw = await claude_complete(prompt, max_tokens=1800, temperature=0.88)
    except Exception as exc:
        log.warning("branding.generate_variants_claude_error", err=str(exc))
        raise HTTPException(
            status_code=502, detail="AI generation failed — retry or contact support"
        ) from exc

    # Strip markdown fences that Claude might add despite instructions
    cleaned = (raw or "").strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^```\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
        variants_raw = parsed.get("variants", [])
    except (json.JSONDecodeError, AttributeError) as exc:
        log.warning(
            "branding.generate_variants_parse_error",
            raw_snippet=cleaned[:300],
            err=str(exc),
        )
        raise HTTPException(
            status_code=502, detail="Could not parse AI response as JSON"
        ) from exc

    variants: list[EmailVariant] = []
    for v in variants_raw[: body.count]:
        if not isinstance(v, dict):
            continue
        variants.append(
            EmailVariant(
                subject=str(v.get("subject", "")).strip()[:60],
                preheader=str(v.get("preheader", "")).strip()[:90],
                body_preview=str(v.get("body_preview", "")).strip(),
                rationale=str(v.get("rationale", "")).strip(),
            )
        )

    if not variants:
        raise HTTPException(status_code=502, detail="AI returned no valid variants")

    log.info(
        "branding.variants_generated",
        tenant_id=tenant_id,
        subject_type=body.subject_type,
        tone=body.tone,
        count=len(variants),
    )
    return GenerateVariantsResponse(
        variants=variants,
        subject_type=body.subject_type,
        tone=body.tone,
    )


# ============================================================
# D. AI full-email regeneration (B.14)
# ============================================================


class RegenerateEmailRequest(BaseModel):
    subject_type: Literal["b2b", "b2c"] = "b2c"
    save: bool = True   # persist style + copy to tenant settings


class RegenerateEmailResponse(BaseModel):
    style: str          # classic | bold | minimal
    subject: str
    headline: str
    main_copy_1: str
    main_copy_2: str
    cta_text: str
    rationale: str      # 1-sentence explanation of choices


@router.post("/regenerate-email", response_model=RegenerateEmailResponse)
async def regenerate_email_template(
    ctx: CurrentUser, body: RegenerateEmailRequest
) -> RegenerateEmailResponse:
    """Generate a complete email design + copy combo via Claude.

    Claude picks the best visual style (classic/bold/minimal) and writes
    all copy components anchored on the tenant's brand and segment.
    If ``body.save=True`` the result is persisted to
    ``tenants.settings.email_copy_overrides`` and
    ``tenants.settings.email_style``.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    t_res = (
        sb.table("tenants")
        .select("business_name, brand_primary_color, settings")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    if not t_res.data:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant = t_res.data[0]
    tenant_name = (tenant.get("business_name") or "la tua azienda").strip()
    brand_color = tenant.get("brand_primary_color") or _DEFAULT_BRAND_COLOR

    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="AI not configured")

    segment_desc = (
        "titolari e responsabili acquisti di aziende (B2B)"
        if body.subject_type == "b2b"
        else "proprietari di abitazioni private (B2C)"
    )

    prompt = f"""Sei un esperto di email marketing e design per il settore fotovoltaico in Italia.
Il brand si chiama "{tenant_name}", colore principale: {brand_color}.
Segmento destinatari: {segment_desc}.

Genera un'email di outreach completa scegliendo:
1. Lo stile visivo più adatto tra:
   - "classic": card bianca, barra colorata in cima, CTA arrotondato — professionale e rassicurante
   - "bold": header con sfondo gradiente nel colore brand, titolo bianco su sfondo scuro — impattante e moderno
   - "minimal": layout editoriale, font serif, senza box, CTA come link con sottolineatura — elegante e discreto

2. Il contenuto testuale:
   - subject: oggetto email (max 60 caratteri, no emoji)
   - headline: H1 dell'email (max 80 caratteri)
   - main_copy_1: primo paragrafo del corpo (2-3 frasi, max 200 caratteri)
   - main_copy_2: secondo paragrafo del corpo (1-2 frasi, max 160 caratteri)
   - cta_text: testo del pulsante CTA (max 40 caratteri)
   - rationale: 1 frase su perché hai scelto questo stile e tono

Vincoli:
- Non citare prezzi fissi né "gratis" / "GRATUITO"
- Stile e tono devono essere coerenti (bold → copy energico; minimal → copy raffinato; classic → copy diretto)
- Il colore brand {brand_color} verrà applicato automaticamente
- Tutti i campi in italiano

Rispondi SOLO con JSON valido, senza markdown né ```json```:
{{"style":"…","subject":"…","headline":"…","main_copy_1":"…","main_copy_2":"…","cta_text":"…","rationale":"…"}}"""

    try:
        raw = await claude_complete(prompt, max_tokens=900, temperature=0.82)
    except Exception as exc:
        log.warning("branding.regenerate_email_claude_error", err=str(exc))
        raise HTTPException(
            status_code=502, detail="AI generation failed — retry or contact support"
        ) from exc

    cleaned = (raw or "").strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^```\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, AttributeError) as exc:
        log.warning(
            "branding.regenerate_email_parse_error",
            raw_snippet=cleaned[:300],
            err=str(exc),
        )
        raise HTTPException(
            status_code=502, detail="Could not parse AI response as JSON"
        ) from exc

    result = RegenerateEmailResponse(
        style=str(parsed.get("style", "classic")).strip(),
        subject=str(parsed.get("subject", "")).strip()[:60],
        headline=str(parsed.get("headline", "")).strip()[:80],
        main_copy_1=str(parsed.get("main_copy_1", "")).strip()[:200],
        main_copy_2=str(parsed.get("main_copy_2", "")).strip()[:160],
        cta_text=str(parsed.get("cta_text", "Vedi il preventivo")).strip()[:40],
        rationale=str(parsed.get("rationale", "")).strip(),
    )

    # Validate style field
    if result.style not in {"classic", "bold", "minimal"}:
        result = result.model_copy(update={"style": "classic"})

    if body.save:
        current_settings: dict[str, Any] = dict(tenant.get("settings") or {})
        updated_settings = {
            **current_settings,
            "email_style": result.style,
            "email_copy_overrides": {
                "headline": result.headline,
                "main_copy_1": result.main_copy_1,
                "main_copy_2": result.main_copy_2,
                "cta_text": result.cta_text,
            },
        }
        sb.table("tenants").update({"settings": updated_settings}).eq("id", tenant_id).execute()
        log.info(
            "branding.email_regenerated",
            tenant_id=tenant_id,
            style=result.style,
            subject_type=body.subject_type,
        )

    return result


# ============================================================
# Resend API helpers
# ============================================================


def _resend_headers() -> dict[str, str]:
    if not settings.resend_api_key:
        raise HTTPException(status_code=503, detail="Resend API key not configured")
    return {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }


async def _fetch_domain_status(
    domain_id: str, *, trigger_verify: bool = False
) -> DomainStatusResponse:
    """GET /domains/{id} → DomainStatusResponse.

    When ``trigger_verify=True`` we first fire ``POST /domains/{id}/verify``
    to ask Resend to re-check the DNS records immediately rather than waiting
    for their background polling interval (up to 24 h).  The verify call is
    best-effort: we log failures but never let them block the status read.
    """
    if not settings.resend_api_key:
        raise HTTPException(status_code=503, detail="Resend API key not configured")

    async with httpx.AsyncClient(timeout=15.0) as client:
        if trigger_verify:
            try:
                vresp = await client.post(
                    f"{_RESEND_API}/domains/{domain_id}/verify",
                    headers=_resend_headers(),
                )
                log.info(
                    "branding.domain_verify_triggered",
                    domain_id=domain_id,
                    status=vresp.status_code,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "branding.domain_verify_trigger_failed",
                    domain_id=domain_id,
                    err=str(exc),
                )

        resp = await client.get(
            f"{_RESEND_API}/domains/{domain_id}",
            headers=_resend_headers(),
        )

    if resp.status_code == 404:
        raise HTTPException(
            status_code=404, detail="Domain not found on Resend — it may have been deleted"
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Resend domain status error: {resp.status_code}",
        )

    data = resp.json()

    # Resend has shipped schema changes over time — `records` used to be
    # at the top level, newer payloads nest it under `dns_records` or
    # return it as null for freshly-created domains. Parse defensively
    # and log the raw payload shape if anything blows up, so the next
    # regression is diagnosable without prod access.
    try:
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            # Some Resend responses wrap the payload: {"object": "domain", "data": {...}}
            data = data["data"]

        records_raw = data.get("records")
        if records_raw is None:
            records_raw = data.get("dns_records") or []
        if not isinstance(records_raw, list):
            records_raw = []

        def _opt_int(v: Any) -> int | None:
            if v is None or v == "":
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        dns_records: list[DnsRecord] = []
        for r in records_raw:
            if not isinstance(r, dict):
                continue
            dns_records.append(
                DnsRecord(
                    type=str(r.get("type") or "").upper(),
                    name=str(r.get("name") or ""),
                    value=str(r.get("value") or r.get("record") or ""),
                    priority=_opt_int(r.get("priority")),
                    ttl=_opt_int(r.get("ttl")),
                    status=str(r.get("status") or "not_started"),
                )
            )

        return DomainStatusResponse(
            domain_id=domain_id,
            domain=str(data.get("name") or ""),
            status=str(data.get("status") or "not_started"),
            dns_records=dns_records,
            created_at=(
                str(data["created_at"]) if data.get("created_at") else None
            ),
        )
    except Exception as exc:  # noqa: BLE001
        # Never let a parser mismatch crash the request — it leaks as
        # "Failed to fetch" on the client because Railway closes the
        # socket mid-ASGI. Log the raw payload (truncated) and return
        # a typed 502 so the frontend shows something actionable.
        log.exception(
            "branding.domain_status_parse_failed",
            domain_id=domain_id,
            payload_preview=str(data)[:500],
            err=str(exc),
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "Risposta inattesa da Resend durante la lettura dello stato del "
                "dominio. Il dominio è stato registrato correttamente — riprova "
                "'Ricontrolla' tra qualche secondo."
            ),
        ) from exc


async def _find_domain_by_name(domain_name: str) -> dict[str, Any] | None:
    """List all Resend domains and return the one matching `domain_name`.

    Normalises trailing dots before comparison so that DNS-style names
    such as ``"example.com."`` still match ``"example.com"``.
    """
    if not settings.resend_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                f"{_RESEND_API}/domains",
                headers=_resend_headers(),
            )
        log.debug(
            "branding.find_domain_list",
            status=resp.status_code,
            body_preview=resp.text[:200],
        )
        if resp.status_code != 200:
            log.warning("branding.find_domain_list_failed", status=resp.status_code)
            return None
        payload = resp.json()
        # Resend returns {"data": [...]} — handle both shapes defensively.
        domains: list[Any] = (
            payload.get("data", []) if isinstance(payload, dict) else payload
        ) or []
        needle = domain_name.lower().rstrip(".")
        for d in domains:
            if not isinstance(d, dict):
                continue
            candidate = d.get("name", "").lower().rstrip(".")
            if candidate == needle:
                return d
        log.warning(
            "branding.find_domain_not_in_list",
            domain=domain_name,
            total_domains=len(domains),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("branding.find_domain_failed", err=str(exc))
    return None
