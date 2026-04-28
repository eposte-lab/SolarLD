"""Email template renderer — Jinja2 + premailer CSS inlining.

Templates live under ``packages/templates/email/`` and are shared
resources across apps (the lead portal might want to reuse them too).
Each outreach template pair is ``outreach_{tier}.html.j2`` +
``outreach_{tier}.txt.j2``:

    * ``outreach_b2b``  — business-toned copy, optional ATECO mention
    * ``outreach_b2c``  — residential-toned copy

Rendering pipeline:

    1. Load Jinja2 env from the templates dir (cached).
    2. Render both .html.j2 and .txt.j2 with the same context.
    3. Run the HTML through ``premailer.transform`` so every CSS rule
       gets inlined onto the matching tag — Gmail in particular strips
       <style> blocks in some preview modes.
    4. Return ``RenderedEmail(subject, html, text)``.

Pure function: no HTTP, no DB, no side effects. Easy to snapshot-test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from premailer import transform as premailer_transform

from ..core.logging import get_logger

log = get_logger(__name__)

# Templates are bundled inside the Python package at
# ``apps/api/src/email_templates/`` so they ship with the Docker image
# via ``COPY src ./src``. ``parents[1]`` resolves to ``/app/src/`` in
# the container and ``apps/api/src/`` in local dev — both valid.
_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "email_templates"


@dataclass(slots=True, frozen=True)
class RenderedEmail:
    subject: str
    html: str
    text: str


@dataclass(slots=True, frozen=True)
class OutreachContext:
    """Everything a template variant needs to render.

    Kept to primitives + small dicts so it's trivially JSON-
    serialisable for audit events and so tests can build instances
    without importing Supabase fixtures.
    """

    tenant_name: str
    brand_primary_color: str                  # hex
    greeting_name: str
    lead_url: str
    optout_url: str
    subject_template: str                     # pre-rendered or static subject
    subject_type: str                         # b2b | b2c | unknown
    roi: dict[str, Any] | None = None         # leads.roi_data projection
    hero_image_url: str | None = None
    hero_gif_url: str | None = None
    personalized_opener: str | None = None    # 1-sentence from Claude (optional)
    business_name: str | None = None          # B2B only
    ateco_code: str | None = None
    ateco_description: str | None = None
    # Follow-up step: 1 = initial, 2 = day-4 nudge, 3 = day-9 case-study,
    # 4 = day-14 breakup email. Picks the right ``{stem}_step{N}.*.j2``.
    sequence_step: int = 1
    # ---- Visual style & AI-generated copy overrides (B.14) ----
    # template_style selects the visual layout applied in _base.html.j2.
    template_style: str = "classic"     # classic | bold | minimal
    headline: str | None = None         # H1 override (AI-generated)
    main_copy_1: str | None = None      # First body paragraph override
    main_copy_2: str | None = None      # Second body paragraph override
    cta_text: str | None = None         # CTA button label override
    brand_logo_url: str | None = None   # Absolute URL to tenant logo
    # ---- Sprint 6.3 additions ----
    # email_style controls which template family to pick:
    #   "visual_preventivo"  — rich HTML with hero image + ROI card (default)
    #   "plain_conversational" — 60-80 word plain-text-feel HTML, cold B2B
    #   "premium"            — Sprint 9 single-column 600px premium template
    email_style: str = "visual_preventivo"
    # Extra context for conversational templates.
    sender_first_name: str | None = None   # e.g. "Alfonso" (from inbox display_name)
    hq_province: str | None = None         # e.g. "Napoli"
    ateco_desc: str | None = None          # Short ATECO description for opener
    recipient_email: str | None = None     # Used in GDPR footer
    tenant_legal_name: str | None = None   # Legal entity name for GDPR footer
    tenant_vat_number: str | None = None   # P.IVA for GDPR footer
    tenant_legal_address: str | None = None  # Registered address for GDPR footer
    similar_province: str | None = None    # Step-3 case study province hint
    video_landing_url: str | None = None   # Sprint 4: /lead/[slug]/video page
    # ---- Sprint 9 additions ----
    # Accent brand color for premium template stat borders + CTA chip.
    brand_color_accent: str | None = None  # e.g. "#F4A300" (gold)
    # Tracking pixel URL for open tracking (1x1 transparent pixel).
    tracking_pixel_url: str | None = None
    # 4 A/B copy variables (injected from cluster_copy_variants row).
    # When null, the Jinja templates fall back to built-in defaults.
    copy_subject: str | None = None
    copy_opening_line: str | None = None
    copy_proposition_line: str | None = None
    cta_primary_label: str | None = None
    # Optional dict with extra copy overrides (from A/B variant row).
    # These are merged into the Jinja context and take precedence over
    # the individual fields above.
    copy_overrides: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def render_outreach_email(ctx: OutreachContext) -> RenderedEmail:
    """Render both the HTML and plain-text bodies for one outreach email."""
    env = _env()
    template_stem = _template_stem_for(
        ctx.subject_type,
        ctx.sequence_step,
        email_style=ctx.email_style,
    )

    # Extract sender first name from the full display name when not explicit.
    sender_first = ctx.sender_first_name
    if not sender_first:
        sender_first = (ctx.tenant_name or "").split()[0] if ctx.tenant_name else None

    context: dict[str, Any] = {
        "subject": ctx.subject_template,
        "email_subject": ctx.subject_template,
        "tenant_name": ctx.tenant_name,
        "brand_primary_color": ctx.brand_primary_color,
        "brand_logo_url": ctx.brand_logo_url,
        "greeting_name": ctx.greeting_name,
        "lead_url": ctx.lead_url,
        "optout_url": ctx.optout_url,
        "roi": ctx.roi,
        "hero_image_url": ctx.hero_image_url,
        "hero_gif_url": ctx.hero_gif_url,
        "personalized_opener": ctx.personalized_opener,
        "business_name": ctx.business_name,
        "ateco_code": ctx.ateco_code,
        "ateco_description": ctx.ateco_description,
        "sequence_step": ctx.sequence_step,
        # Style & AI copy overrides
        "template_style": ctx.template_style,
        "headline": ctx.headline,
        "main_copy_1": ctx.main_copy_1,
        "main_copy_2": ctx.main_copy_2,
        "cta_text": ctx.cta_text,
        # Sprint 6.3: conversational template extras
        "email_style": ctx.email_style,
        "sender_first_name": sender_first,
        "hq_province": ctx.hq_province,
        "ateco_desc": ctx.ateco_desc or ctx.ateco_description,
        "recipient_email": ctx.recipient_email or "",
        "tenant_legal_name": ctx.tenant_legal_name or ctx.tenant_name,
        "tenant_vat_number": ctx.tenant_vat_number,
        "tenant_legal_address": ctx.tenant_legal_address,
        "similar_province": ctx.similar_province,
        "video_landing_url": ctx.video_landing_url,
        # Sprint 9: premium template extras + A/B copy variables
        "brand_color_accent": ctx.brand_color_accent or "#F4A300",
        "tracking_pixel_url": ctx.tracking_pixel_url,
        "copy_subject": ctx.copy_subject,
        "copy_opening_line": ctx.copy_opening_line,
        "copy_proposition_line": ctx.copy_proposition_line,
        "cta_primary_label": ctx.cta_primary_label,
    }

    # Apply copy_overrides last — A/B variant fields override everything.
    if ctx.copy_overrides:
        context.update(ctx.copy_overrides)

    html_raw = env.get_template(f"{template_stem}.html.j2").render(**context)
    text_raw = env.get_template(f"{template_stem}.txt.j2").render(**context)

    # premailer inlines <style> rules and rewrites relative URLs.
    # `keep_style_tags=True` keeps the original <style> around as a
    # fallback for clients that support it (e.g. Apple Mail for media
    # queries we might add later).
    html_inlined = premailer_transform(
        html_raw,
        keep_style_tags=True,
        remove_classes=False,
        disable_validation=True,
    )

    return RenderedEmail(
        subject=ctx.subject_template,
        html=html_inlined,
        text=text_raw.strip() + "\n",
    )


async def render_outreach_email_with_fallback(
    ctx: OutreachContext,
    *,
    supabase: Any | None = None,
    tenant_row: dict[str, Any] | None = None,
) -> RenderedEmail:
    """Render with the 3-tier fallback chain (Sprint 9 Fase C.4).

    Tier 1 — Tenant custom HTML (active + uploaded to Supabase Storage):
        Only attempted when ``supabase`` and ``tenant_row`` are provided
        and ``tenant_row["custom_email_template_active"]`` is True.
        Falls through to tier 2 on any error (log + skip).

    Tier 2 — Premium SolarLead template (``outreach_solarld_premium*``):
        Default for all new tenants (``email_template_family='premium'``).
        Uses ``render_outreach_email(ctx)`` with email_style='premium'.

    Tier 3 — Legacy visual / conversational:
        For tenants with ``email_template_family='legacy_visual'`` or
        ``'plain_conversational'``.  Uses ``render_outreach_email(ctx)``
        with the original email_style from ``ctx``.

    ``copy_overrides`` (from the cluster A/B engine) are applied in all
    three tiers — the custom template decides whether to use the variables
    or not; the built-in templates use ``| default(...)`` filters.

    Args:
        ctx: Fully-built OutreachContext (including copy_overrides).
        supabase: Service-role Supabase async client (optional).
        tenant_row: Raw tenant dict from DB (optional).

    Returns:
        RenderedEmail with subject, html, text.
    """
    # ── Tier 1: custom HTML ──────────────────────────────────────────
    if (
        supabase is not None
        and tenant_row is not None
        and tenant_row.get("custom_email_template_active")
    ):
        template_path: str | None = tenant_row.get("custom_email_template_path")
        if template_path:
            try:
                from .custom_template_service import render_custom_template

                # Build the same flat context dict we pass to built-in templates.
                sender_first = ctx.sender_first_name
                if not sender_first:
                    sender_first = (ctx.tenant_name or "").split()[0] if ctx.tenant_name else None

                flat_ctx: dict[str, Any] = {
                    "subject": ctx.subject_template,
                    "email_subject": ctx.subject_template,
                    "tenant_name": ctx.tenant_name,
                    "brand_primary_color": ctx.brand_primary_color,
                    "brand_logo_url": ctx.brand_logo_url,
                    "greeting_name": ctx.greeting_name,
                    "lead_url": ctx.lead_url,
                    "optout_url": ctx.optout_url,
                    "unsubscribe_url": ctx.optout_url,   # alias used in GDPR footer
                    "roi": ctx.roi,
                    "hero_image_url": ctx.hero_image_url,
                    "hero_gif_url": ctx.hero_gif_url,
                    "personalized_opener": ctx.personalized_opener,
                    "business_name": ctx.business_name,
                    "ateco_code": ctx.ateco_code,
                    "ateco_description": ctx.ateco_description,
                    "sequence_step": ctx.sequence_step,
                    "template_style": ctx.template_style,
                    "headline": ctx.headline,
                    "main_copy_1": ctx.main_copy_1,
                    "main_copy_2": ctx.main_copy_2,
                    "cta_text": ctx.cta_text,
                    "email_style": ctx.email_style,
                    "sender_first_name": sender_first,
                    "hq_province": ctx.hq_province,
                    "ateco_desc": ctx.ateco_desc or ctx.ateco_description,
                    "recipient_email": ctx.recipient_email or "",
                    "tenant_legal_name": ctx.tenant_legal_name or ctx.tenant_name,
                    "tenant_vat_number": ctx.tenant_vat_number,
                    "tenant_legal_address": ctx.tenant_legal_address,
                    "similar_province": ctx.similar_province,
                    "video_landing_url": ctx.video_landing_url,
                    "brand_color_accent": ctx.brand_color_accent or "#F4A300",
                    "tracking_pixel_url": ctx.tracking_pixel_url,
                    "copy_subject": ctx.copy_subject,
                    "copy_opening_line": ctx.copy_opening_line,
                    "copy_proposition_line": ctx.copy_proposition_line,
                    "cta_primary_label": ctx.cta_primary_label,
                }
                if ctx.copy_overrides:
                    flat_ctx.update(ctx.copy_overrides)

                html_inlined = await render_custom_template(
                    supabase,
                    tenant_row.get("id", ""),
                    template_path,
                    flat_ctx,
                )
                # Plain-text fallback: strip tags with a very small util
                text = _strip_html_to_text(html_inlined, ctx)
                log.info(
                    "email_template.custom_tier_used",
                    tenant_id=tenant_row.get("id"),
                    template_path=template_path,
                )
                return RenderedEmail(
                    subject=ctx.subject_template,
                    html=html_inlined,
                    text=text,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "email_template.custom_tier_failed_falling_back",
                    template_path=template_path,
                    error=str(exc),
                )

    # ── Tier 2 / 3: built-in templates (sync) ───────────────────────
    # Respect per-tenant email_template_family when present.
    effective_style = ctx.email_style
    if tenant_row is not None:
        family = tenant_row.get("email_template_family")
        if family == "legacy_visual":
            effective_style = "visual_preventivo"
        elif family == "plain_conversational":
            effective_style = "plain_conversational"
        # family == "premium" or None → keep ctx.email_style (already 'premium' by default)

    if effective_style != ctx.email_style:
        import dataclasses
        ctx = dataclasses.replace(ctx, email_style=effective_style)

    return render_outreach_email(ctx)


def _strip_html_to_text(html: str, ctx: OutreachContext) -> str:
    """Very light HTML → plain-text for custom template text part.

    We don't want a full HTML→text lib (e.g. html2text adds Markdown
    noise). For custom templates we just produce a short plain-text that
    contains the tenant name, the lead URL and the optout URL — the
    legal minimum for a CAN-SPAM/GDPR compliant plain-text part.
    """
    lines = [
        f"{ctx.tenant_name}",
        "",
        ctx.copy_opening_line or ctx.personalized_opener or "",
        ctx.copy_proposition_line or "",
        "",
        f"Apri la tua analisi: {ctx.lead_url}",
        "",
        f"Per non ricevere ulteriori email: {ctx.optout_url}",
        ctx.tenant_legal_name or ctx.tenant_name or "",
    ]
    if ctx.tenant_vat_number:
        lines.append(f"P.IVA {ctx.tenant_vat_number}")
    if ctx.tenant_legal_address:
        lines.append(ctx.tenant_legal_address)
    return "\n".join(lines) + "\n"


def default_subject_for(
    subject_type: str,
    tenant_name: str,
    *,
    sequence_step: int = 1,
    email_style: str = "visual_preventivo",
    sender_first_name: str | None = None,
) -> str:
    """Sensible default subject line per template variant & sequence step.

    Conversational templates use shorter, first-person subjects that feel
    like a real human sent them — not a bulk mailer.
    Step 2/3/4 lines differ so Gmail doesn't collapse the thread.
    """
    st = (subject_type or "").lower()
    sender = sender_first_name or tenant_name

    if email_style == "premium":
        # Premium subjects are crisp and informative — delegate to B2B visual
        # defaults as a sensible baseline; A/B copy_subject overrides these at
        # render time (the OutreachContext.copy_subject field takes precedence).
        if st == "b2b":
            subjects_premium = {
                1: f"{tenant_name} — analisi fotovoltaica per la vostra sede",
                2: f"Re: analisi fotovoltaica — i numeri chiave",
                3: f"Un caso reale nel vostro settore",
                4: f"Chiudo il vostro caso — buon lavoro",
            }
            return subjects_premium.get(sequence_step, subjects_premium[1])
        return f"{tenant_name} — analisi fotovoltaica personalizzata"

    if email_style == "plain_conversational":
        # Short, personal subjects — no brand prefix.
        if st == "b2b":
            subjects_conv = {
                1: "Fotovoltaico per la vostra sede",
                2: "Re: fotovoltaico per la vostra sede",
                3: "Un dato su risparmio energetico",
                4: "Chiudo il caso",
            }
            return subjects_conv.get(sequence_step, subjects_conv[1])
        # B2C conversational (future)
        return "Una proposta per la vostra casa"

    # ── Visual / preventivo style (legacy default) ──────────────────────
    step = sequence_step if sequence_step in {1, 2, 3, 4} else 1

    if st == "b2b":
        base = f"{tenant_name} — simulazione impianto fotovoltaico sulla vostra sede"
        if step == 2:
            return f"{tenant_name} — numeri e rendering per la vostra sede"
        if step == 3:
            return f"{tenant_name} — ultima occasione di rivedere i numeri"
        if step == 4:
            return f"Re: fotovoltaico per la vostra sede"
        return base
    if st == "b2c":
        base = (
            f"{tenant_name} — ecco come potrebbe essere la vostra casa "
            f"con il fotovoltaico"
        )
        if step == 2:
            return f"{tenant_name} — un rendering del vostro tetto vi aspetta"
        if step == 3:
            return f"{tenant_name} — ultimo promemoria sul rendering del tetto"
        if step == 4:
            return f"Re: fotovoltaico per la vostra casa"
        return base
    return f"{tenant_name} — simulazione fotovoltaica"


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


_env_cache: Environment | None = None


def _env() -> Environment:
    global _env_cache
    if _env_cache is None:
        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=select_autoescape(["html", "htm", "xml", "j2"]),
            undefined=StrictUndefined,
            trim_blocks=False,
            lstrip_blocks=False,
        )
        env.filters["format_money"] = _format_money
        _env_cache = env
    return _env_cache


def _format_money(value: Any) -> str:
    """Format a numeric as Italian thousand-dot grouping, no decimals."""
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return str(value)
    # Italian convention: dot as thousand separator. Build manually
    # because locale-based formatting is brittle across machines.
    sign = "-" if n < 0 else ""
    n = abs(n)
    s = f"{n:,}".replace(",", ".")
    return sign + s


# ---------------------------------------------------------------------------
# Engagement-based follow-up scenarios (Sprint 10)
# ---------------------------------------------------------------------------

# Allowed scenario stems — keep aligned with files in email_templates/.
_FOLLOWUP_SCENARIOS = frozenset(
    {"cold", "lukewarm", "engaged", "interessato", "riattivazione"}
)

# Default subject lines per scenario. The cron may override these via
# ``ctx.subject_template`` if A/B copy is supplied.
_FOLLOWUP_DEFAULT_SUBJECTS: dict[str, str] = {
    "cold": "Un piccolo aggiornamento sul fotovoltaico",
    "lukewarm": "Tre numeri sull'autoconsumo che meritano un'occhiata",
    "engaged": "Un aggiornamento dal vostro settore",
    "interessato": "Una proposta concreta — 15 minuti",
    "riattivazione": "Aggiorniamo i numeri quando vi serve?",
}


def render_followup_email(
    ctx: OutreachContext,
    *,
    scenario: str,
    sector_news: dict[str, Any] | None = None,
) -> RenderedEmail:
    """Render an engagement-based follow-up email for a given scenario.

    Unlike ``render_outreach_email`` (which is keyed by subject_type +
    sequence_step + email_style), the followup templates are scenario-
    keyed and orthogonal to the b2b/b2c axis. Templates live at
    ``email_templates/followup_{scenario}.{html,txt}.j2``.

    ``sector_news`` is an optional dict from ``sector_news_service.pick_news``;
    when present it is exposed to the template as ``sector_news_headline``,
    ``sector_news_body``, ``sector_news_source_url`` so the copy can quote
    a sector-relevant fact instead of mentioning tracked behaviour.
    """
    if scenario not in _FOLLOWUP_SCENARIOS:
        raise ValueError(f"unknown_followup_scenario:{scenario}")

    env = _env()
    template_stem = f"followup_{scenario}"

    sender_first = ctx.sender_first_name
    if not sender_first:
        sender_first = (ctx.tenant_name or "").split()[0] if ctx.tenant_name else None

    # Default subject if caller didn't override.
    subject = ctx.subject_template or _FOLLOWUP_DEFAULT_SUBJECTS.get(
        scenario, "Aggiornamento"
    )
    if ctx.copy_subject:
        subject = ctx.copy_subject

    context: dict[str, Any] = {
        "subject": subject,
        "email_subject": subject,
        "tenant_name": ctx.tenant_name,
        "brand_primary_color": ctx.brand_primary_color,
        "brand_logo_url": ctx.brand_logo_url,
        "greeting_name": ctx.greeting_name,
        "lead_url": ctx.lead_url,
        "optout_url": ctx.optout_url,
        "roi": ctx.roi,
        "hero_image_url": ctx.hero_image_url,
        "hero_gif_url": ctx.hero_gif_url,
        "personalized_opener": ctx.personalized_opener,
        "business_name": ctx.business_name,
        "ateco_code": ctx.ateco_code,
        "ateco_description": ctx.ateco_description,
        "headline": ctx.headline,
        "main_copy_1": ctx.main_copy_1,
        "main_copy_2": ctx.main_copy_2,
        "cta_text": ctx.cta_text,
        "sender_first_name": sender_first,
        "hq_province": ctx.hq_province,
        "ateco_desc": ctx.ateco_desc or ctx.ateco_description,
        "recipient_email": ctx.recipient_email or "",
        "tenant_legal_name": ctx.tenant_legal_name or ctx.tenant_name,
        "tenant_vat_number": ctx.tenant_vat_number,
        "tenant_legal_address": ctx.tenant_legal_address,
        "video_landing_url": ctx.video_landing_url,
        "brand_color_accent": ctx.brand_color_accent or "#F4A300",
        "tracking_pixel_url": ctx.tracking_pixel_url,
        "copy_subject": ctx.copy_subject,
        "copy_opening_line": ctx.copy_opening_line,
        "copy_proposition_line": ctx.copy_proposition_line,
        "cta_primary_label": ctx.cta_primary_label,
        # Scenario-specific extras
        "scenario": scenario,
        "sector_news_headline": (sector_news or {}).get("headline"),
        "sector_news_body": (sector_news or {}).get("body"),
        "sector_news_source_url": (sector_news or {}).get("source_url"),
    }

    if ctx.copy_overrides:
        context.update(ctx.copy_overrides)

    html_raw = env.get_template(f"{template_stem}.html.j2").render(**context)
    text_raw = env.get_template(f"{template_stem}.txt.j2").render(**context)

    html_inlined = premailer_transform(
        html_raw,
        keep_style_tags=True,
        remove_classes=False,
        disable_validation=True,
    )

    return RenderedEmail(
        subject=subject,
        html=html_inlined,
        text=text_raw.strip() + "\n",
    )


def _template_stem_for(
    subject_type: str,
    sequence_step: int = 1,
    *,
    email_style: str = "visual_preventivo",
) -> str:
    """Resolve the template stem for this send.

    email_style='premium' picks the Sprint 9 ``outreach_solarld_premium``
    family (steps 1-4, all B2B/B2C compatible). Fallback chain:
      - premium step N → step 1 premium → plain_conversational

    email_style='plain_conversational' picks the ``outreach_conversational_b2b``
    family (steps 1-4). Fallback chain:
      - conversational step N → step 1 conversational → visual step N → visual step 1

    email_style='visual_preventivo' (default) keeps the legacy ``outreach_b2b``
    family (steps 1-3) unchanged.
    """
    st = (subject_type or "").lower()
    if st not in {"b2b", "b2c"}:
        st = "b2c"
    step = max(1, min(4, int(sequence_step or 1)))
    env = _env()

    # ── Premium family (Sprint 9) ──────────────────────────────────────
    if email_style == "premium":
        if step == 1:
            cand = "outreach_solarld_premium"
        else:
            cand = f"outreach_solarld_premium_step{step}"
        if f"{cand}.html.j2" in env.list_templates():
            return cand
        # Step N missing → fall back to step 1 premium.
        if "outreach_solarld_premium.html.j2" in env.list_templates():
            return "outreach_solarld_premium"
        # Ultimate fallback: plain conversational.
        if st == "b2b" and "outreach_conversational_b2b.html.j2" in env.list_templates():
            return "outreach_conversational_b2b"
        return "outreach_b2b"

    if email_style == "plain_conversational" and st == "b2b":
        # Conversational family: outreach_conversational_b2b[_step{N}]
        if step == 1:
            cand = "outreach_conversational_b2b"
        else:
            cand = f"outreach_conversational_b2b_step{step}"
        if f"{cand}.html.j2" in env.list_templates():
            return cand
        # Step N missing → fall back to step 1 conversational.
        if "outreach_conversational_b2b.html.j2" in env.list_templates():
            return "outreach_conversational_b2b"
        # Ultimate fallback: visual b2b.
        return "outreach_b2b"

    # ── Visual / preventivo (legacy) ──────────────────────────────────
    # Step 4 maps to step 3 for visual templates (no dedicated breakup copy).
    legacy_step = step if step in {2, 3} else (3 if step == 4 else 1)
    if legacy_step == 1:
        return f"outreach_{st}"
    stem = f"outreach_{st}_step{legacy_step}"
    if f"{stem}.html.j2" in env.list_templates():
        return stem
    return f"outreach_{st}"
