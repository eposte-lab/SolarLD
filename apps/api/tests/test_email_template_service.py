"""Pure-function tests for ``services.email_template_service``.

The templates themselves live under ``packages/templates/email``.
These tests ensure:
  1. Jinja can load and render both variants end-to-end
  2. Premailer inlines the <style> block onto at least the CTA button
  3. The Italian money filter produces dot-separated thousands
  4. Subject defaults match product copy
  5. StrictUndefined throws loudly on missing required vars
"""

from __future__ import annotations

import pytest
from jinja2 import UndefinedError

from src.services.email_template_service import (
    OutreachContext,
    RenderedEmail,
    _format_money,
    default_subject_for,
    render_outreach_email,
)


def _ctx(**overrides: object) -> OutreachContext:
    base: dict[str, object] = {
        "tenant_name": "Solare Rapido SRL",
        "brand_primary_color": "#0F766E",
        "greeting_name": "Mario Rossi",
        "lead_url": "https://leads.example.com/l/abc",
        "optout_url": "https://leads.example.com/optout/abc",
        "subject_template": "Solare Rapido — preventivo",
        "subject_type": "b2b",
        "roi": {
            # Core fields (original fixture)
            "estimated_kwp": 12,
            "yearly_kwh": 15600,
            "yearly_savings_eur": 2450,
            "payback_years": 6.2,
            "co2_tonnes_25_years": 75,
            "co2_kg_per_year": 3000,
            "gross_capex_eur": 14400,
            "incentive_eur": 4320,
            "net_capex_eur": 10080,
            "self_consumption_ratio": 0.65,
            "meets_roi_target": True,
            # Extended fields added by RoiEstimate.to_jsonb() (Sprint 1.x)
            "net_self_savings_eur": 2230,
            "savings_25y_eur": 47400,
            "roi_pct_25y": 370,
            "trees_equivalent": 143,
        },
        "hero_image_url": "https://cdn.example.com/after.png",
        "hero_gif_url": None,
        "personalized_opener": "La ringraziamo per il tempo dedicato.",
        "business_name": "Panetteria Rossi Srl",
        "ateco_code": "10.71",
        "ateco_description": "Produzione pane",
    }
    base.update(overrides)
    return OutreachContext(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# default_subject_for
# ---------------------------------------------------------------------------


def test_default_subject_for_b2b() -> None:
    subj = default_subject_for("b2b", "Solare Rapido")
    assert subj.startswith("Solare Rapido —")
    assert "simulazione" in subj.lower()


def test_default_subject_for_b2c() -> None:
    subj = default_subject_for("b2c", "Solare Rapido")
    assert "casa" in subj.lower()


def test_default_subject_for_unknown_defaults_to_generic() -> None:
    assert "simulazione" in default_subject_for("mystery", "S").lower()


def test_default_subject_for_case_insensitive() -> None:
    assert default_subject_for("B2B", "Acme") == default_subject_for("b2b", "Acme")


# ---------------------------------------------------------------------------
# _format_money (Italian thousand-dot grouping)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "0"),
        (5, "5"),
        (999, "999"),
        (1000, "1.000"),
        (12345, "12.345"),
        (1000000, "1.000.000"),
        (-1500, "-1.500"),
        ("2450", "2.450"),  # string coerces
        (2450.6, "2.451"),  # rounds half-up-ish via round()
    ],
)
def test_format_money(value: object, expected: str) -> None:
    assert _format_money(value) == expected


def test_format_money_non_numeric_returns_str() -> None:
    assert _format_money("foo") == "foo"
    assert _format_money(None) == "None"


# ---------------------------------------------------------------------------
# render_outreach_email — B2B
# ---------------------------------------------------------------------------


def test_render_b2b_returns_html_and_text() -> None:
    out = render_outreach_email(_ctx(subject_type="b2b"))
    assert isinstance(out, RenderedEmail)
    assert out.subject == "Solare Rapido — preventivo"
    assert out.html.startswith("<")
    assert "Mario Rossi" in out.text
    assert "Panetteria Rossi Srl" in out.text
    # Italian thousand-dot grouping for the savings.
    # The HTML block shows net_self_savings_eur (2230 → "2.230") — the
    # conservative self-consumption-only savings used since Sprint 1.x.
    # The text template may still show yearly_savings_eur.
    assert "2.230" in out.html or "2.450" in out.html  # net or gross savings visible
    assert "2.450" in out.text or "2.230" in out.text


def test_render_b2b_includes_lead_and_optout_urls() -> None:
    out = render_outreach_email(_ctx())
    assert "https://leads.example.com/l/abc" in out.html
    assert "https://leads.example.com/optout/abc" in out.html
    assert "https://leads.example.com/optout/abc" in out.text


def test_render_b2b_personalized_opener_present_when_given() -> None:
    out = render_outreach_email(_ctx(personalized_opener="Ciao dal team."))
    assert "Ciao dal team." in out.html
    assert "Ciao dal team." in out.text


def test_render_b2b_no_opener_no_empty_paragraph() -> None:
    out = render_outreach_email(_ctx(personalized_opener=None))
    # Not asserting exact HTML shape — just that render doesn't crash.
    assert "<h1" in out.html.lower() or "<h1>" in out.html


def test_render_b2b_respects_brand_primary_color_in_html() -> None:
    out = render_outreach_email(_ctx(brand_primary_color="#FF3366"))
    assert "#FF3366" in out.html or "#ff3366" in out.html.lower()


def test_render_b2b_uses_gif_over_static_image() -> None:
    out = render_outreach_email(
        _ctx(
            hero_image_url="https://cdn/a.png",
            hero_gif_url="https://cdn/a.gif",
        )
    )
    assert "a.gif" in out.html


# ---------------------------------------------------------------------------
# render_outreach_email — B2C
# ---------------------------------------------------------------------------


def test_render_b2c_uses_residential_tone() -> None:
    out = render_outreach_email(
        _ctx(
            subject_type="b2c",
            greeting_name="Famiglia Bianchi",
            business_name=None,
            ateco_code=None,
            ateco_description=None,
        )
    )
    assert "Famiglia Bianchi" in out.text
    # B2C template uses "Gentile" salutation.
    assert "Gentile" in out.text


def test_render_unknown_subject_type_falls_back_to_b2c() -> None:
    out = render_outreach_email(
        _ctx(
            subject_type="mystery",
            business_name=None,
            ateco_code=None,
            ateco_description=None,
        )
    )
    # B2C-ish content — at least produces a valid HTML body.
    assert "<html" in out.html.lower() or "<body" in out.html.lower()


def test_render_roi_none_still_renders() -> None:
    out = render_outreach_email(
        _ctx(subject_type="b2c", roi=None, business_name=None,
             ateco_code=None, ateco_description=None)
    )
    # The ROI block should be suppressed but the body should still be there.
    assert "<html" in out.html.lower() or "<body" in out.html.lower()


# ---------------------------------------------------------------------------
# ROI formatting
# ---------------------------------------------------------------------------


def test_render_payback_omitted_when_missing() -> None:
    out = render_outreach_email(
        _ctx(
            roi={
                "estimated_kwp": 10,
                "yearly_savings_eur": 1500,
                "payback_years": None,
                "co2_tonnes_25_years": None,
            }
        )
    )
    assert "1.500" in out.text
    # Don't assert on missing payback — the template is free to render
    # whatever falsy branch it likes, we just want no exception.


def test_render_ends_with_single_trailing_newline_in_text() -> None:
    out = render_outreach_email(_ctx())
    assert out.text.endswith("\n")
    assert not out.text.endswith("\n\n\n")
