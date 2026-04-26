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

    context = {
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
    }

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


def _template_stem_for(
    subject_type: str,
    sequence_step: int = 1,
    *,
    email_style: str = "visual_preventivo",
) -> str:
    """Resolve the template stem for this send.

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
