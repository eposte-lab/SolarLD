"""Unit tests for the digest email composition — pure helpers."""

from __future__ import annotations

from src.services.digest_service import (
    DigestStats,
    format_digest_html,
    format_digest_text,
)


def _stats(**overrides: object) -> DigestStats:
    defaults = dict(
        tenant_name="Solare Srl",
        window_label="ultime 24 ore",
        new_leads=5,
        new_hot=2,
        outreach_sent=4,
        outreach_opened=3,
        outreach_clicked=1,
        contracts_signed=1,
        total_cost_eur=12.34,
    )
    defaults.update(overrides)
    return DigestStats(**defaults)  # type: ignore[arg-type]


def test_html_contains_every_metric_label() -> None:
    html = format_digest_html(_stats())
    for label in [
        "Nuovi lead",
        "HOT",
        "Email inviate",
        "Email aperte",
        "Email cliccate",
        "Contratti firmati",
    ]:
        assert label in html


def test_html_embeds_tenant_name_and_window_label() -> None:
    html = format_digest_html(_stats(tenant_name="Fotovoltaici srl"))
    assert "Fotovoltaici srl" in html
    assert "ultime 24 ore" in html


def test_text_variant_is_plain_and_complete() -> None:
    text = format_digest_text(_stats(new_leads=10, contracts_signed=3))
    assert "<" not in text and "</" not in text  # no HTML leaked in
    assert "Nuovi lead:" in text
    assert "Contratti firmati:  3" in text
    assert "€12.34" in text


def test_zero_activity_still_renders_safely() -> None:
    html = format_digest_html(
        _stats(
            new_leads=0,
            new_hot=0,
            outreach_sent=0,
            outreach_opened=0,
            outreach_clicked=0,
            contracts_signed=0,
            total_cost_eur=0.0,
        )
    )
    # Layout should still produce a valid-looking HTML with zero rows.
    assert "<html" in html
    assert "€0.00" in html
