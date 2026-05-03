"""Tests for web_scraper — pure logic only (no network)."""

from __future__ import annotations

import pytest

from src.services import web_scraper as ws


# ---------------------------------------------------------------------------
# Email selection
# ---------------------------------------------------------------------------


def test_extract_best_email_picks_named_role_first() -> None:
    out = ws.extract_best_email(
        ["info@acme.it", "direzione@acme.it", "marketing@acme.it"]
    )
    assert out is not None
    assert out.value == "direzione@acme.it"
    assert out.confidence == "alta"
    assert out.type == "named_role"


def test_extract_best_email_falls_back_to_generic() -> None:
    out = ws.extract_best_email(["info@acme.it", "newsletter@acme.it"])
    assert out is not None
    assert out.value == "info@acme.it"
    assert out.confidence == "alta"  # info is in the priority list
    assert out.type == "named_role"


def test_extract_best_email_first_generic_when_no_priority_match() -> None:
    out = ws.extract_best_email(["sales@acme.it", "team@acme.it"])
    assert out is not None
    assert out.value == "sales@acme.it"
    assert out.confidence == "media"
    assert out.type == "generic"


def test_extract_best_email_drops_hard_exclusions() -> None:
    out = ws.extract_best_email(
        ["privacy@acme.it", "noreply@acme.it", "newsletter@acme.it"]
    )
    assert out is None


def test_extract_best_email_empty_input() -> None:
    assert ws.extract_best_email([]) is None


def test_extract_best_email_priority_order_amministrazione_over_info() -> None:
    out = ws.extract_best_email(
        ["info@acme.it", "amministrazione@acme.it"]
    )
    assert out is not None
    # PRIORITA = direzione, amministrazione, info, commerciale
    # amministrazione comes before info → wins.
    assert out.value == "amministrazione@acme.it"


# ---------------------------------------------------------------------------
# HTML parsers
# ---------------------------------------------------------------------------


def test_extract_emails_from_html_simple() -> None:
    html = """
    <html><body>
      <a href="mailto:info@example.it">Contattaci</a>
      <p>direzione@example.it è la direzione</p>
    </body></html>
    """
    emails = ws._extract_emails_from_html(html)
    assert "info@example.it" in emails
    assert "direzione@example.it" in emails


def test_extract_emails_from_html_dedupes_case() -> None:
    html = "Email: Info@Example.IT and info@example.it"
    emails = ws._extract_emails_from_html(html)
    # Both forms collapse to lowercase canonical.
    assert len(emails) == 1


def test_extract_emails_from_html_drops_image_filenames() -> None:
    html = '<img src="logo@2x.png" /> info@example.it'
    emails = ws._extract_emails_from_html(html)
    assert "info@example.it" in emails
    assert all(not e.endswith(".png") for e in emails)


def test_extract_phone_from_html_finds_italian_format() -> None:
    html = "Tel: +39 030 1234567 / 02 9876543"
    phone = ws._extract_phone_from_html(html)
    assert phone is not None
    assert "030" in phone or "9876543" in phone


def test_extract_phone_from_html_returns_none_when_absent() -> None:
    assert ws._extract_phone_from_html("<html>nothing here</html>") is None


# ---------------------------------------------------------------------------
# PEC classification
# ---------------------------------------------------------------------------


def test_classify_pec_finds_legalmail() -> None:
    emails = ["info@acme.it", "acme@legalmail.it"]
    assert ws._classify_pec(emails) == "acme@legalmail.it"


def test_classify_pec_finds_pec_subdomain() -> None:
    emails = ["info@acme.it", "acme@pec.acme.it"]
    assert ws._classify_pec(emails) == "acme@pec.acme.it"


def test_classify_pec_returns_none_for_regular_emails() -> None:
    emails = ["info@acme.it", "direzione@acme.it"]
    assert ws._classify_pec(emails) is None
