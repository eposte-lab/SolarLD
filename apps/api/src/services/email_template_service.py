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
    # Follow-up step: 1 = initial outreach, 2 = day-4 nudge, 3 = day-11
    # last-chance. Picks the right ``outreach_{tier}_step{N}.*.j2``.
    sequence_step: int = 1
    # ---- Visual style & AI-generated copy overrides (B.14) ----
    # template_style selects the visual layout applied in _base.html.j2.
    template_style: str = "classic"     # classic | bold | minimal
    headline: str | None = None         # H1 override (AI-generated)
    main_copy_1: str | None = None      # First body paragraph override
    main_copy_2: str | None = None      # Second body paragraph override
    cta_text: str | None = None         # CTA button label override
    brand_logo_url: str | None = None   # Absolute URL to tenant logo


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def render_outreach_email(ctx: OutreachContext) -> RenderedEmail:
    """Render both the HTML and plain-text bodies for one outreach email."""
    env = _env()
    template_stem = _template_stem_for(ctx.subject_type, ctx.sequence_step)

    context = {
        "subject": ctx.subject_template,
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
    subject_type: str, tenant_name: str, *, sequence_step: int = 1
) -> str:
    """Sensible default subject line per template variant & sequence step.

    Step 2/3 subject lines are slightly different so they don't look
    like pure duplicates in the inbox — Gmail sometimes collapses
    identical-subject threads into one preview.
    """
    st = (subject_type or "").lower()
    step = sequence_step if sequence_step in {1, 2, 3} else 1

    if st == "b2b":
        base = f"{tenant_name} — simulazione impianto fotovoltaico sulla vostra sede"
        if step == 2:
            return f"{tenant_name} — numeri e rendering per la vostra sede"
        if step == 3:
            return f"{tenant_name} — ultima occasione di rivedere i numeri"
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


def _template_stem_for(subject_type: str, sequence_step: int = 1) -> str:
    """Resolve ``outreach_{tier}[_step{N}]`` stem.

    Step 1 uses the bare ``outreach_b2b`` / ``outreach_b2c`` templates
    for backwards compatibility with the Sprint-6 renderer. Steps 2 & 3
    look for ``_step2`` / ``_step3`` suffixed files. If a follow-up
    template is missing we fall back to step-1 copy — better than 500'ing.
    """
    st = (subject_type or "").lower()
    if st not in {"b2b", "b2c"}:
        # Fallback to B2C's softer tone for unknown subjects.
        st = "b2c"
    step = sequence_step if sequence_step in {2, 3} else 1
    if step == 1:
        return f"outreach_{st}"

    stem = f"outreach_{st}_step{step}"
    env = _env()
    # If the follow-up file doesn't exist, degrade to the day-0 template.
    if f"{stem}.html.j2" not in env.list_templates():
        return f"outreach_{st}"
    return stem
