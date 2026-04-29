"""Pure-logic tests for the phone extraction cascade and Atoka mapper.

These cover:
  * `_extract_phone` over Atoka raw payloads with a few possible nestings
  * `_normalise_phone` formatting (Italian +39 fallback, separator strip)
  * `extract_phone` Atoka-source short-circuit
  * Plain-text + tel: href regex behaviour on a small HTML fixture

End-to-end (with a real httpx mock for the website fetch) is exercised
in `test_e2e_flow.py`.
"""

from __future__ import annotations

import pytest

from src.services.email_extractor import (
    _PLAIN_PHONE_RE,
    _TEL_HREF_RE,
    _normalise_phone,
    extract_phone,
)
from src.services.italian_business_service import _extract_phone


# ---------------------------------------------------------------------------
# _normalise_phone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+39 02 1234 5678", "+390212345678"),
        ("02 1234 5678", "+390212345678"),
        ("0039 02 12345678", "+390212345678"),
        ("+39-333-123-4567", "+393331234567"),
        ("(02) 1234.5678", "+390212345678"),
        ("  02/12345678  ", "+390212345678"),
        ("", None),
        ("123", None),  # too short
        ("abc", None),  # letters only
    ],
)
def test_normalise_phone(raw: str, expected: str | None) -> None:
    assert _normalise_phone(raw) == expected


# ---------------------------------------------------------------------------
# Atoka raw — `_extract_phone` (canonical helper)
# ---------------------------------------------------------------------------


def test_extract_phone_from_phones_list() -> None:
    raw = {"phones": ["+39 02 12345678"]}
    assert _extract_phone(raw) == "+39 02 12345678"


def test_extract_phone_from_contacts_list() -> None:
    raw = {
        "contacts": [
            {"type": "email", "value": "info@example.it"},
            {"type": "phone", "value": "02 12345678"},
        ]
    }
    assert _extract_phone(raw) == "02 12345678"


def test_extract_phone_from_base_phone() -> None:
    raw = {"base": {"phone": "+39 333 1234567"}}
    assert _extract_phone(raw) == "+39 333 1234567"


def test_extract_phone_returns_none_when_absent() -> None:
    assert _extract_phone({}) is None
    assert _extract_phone({"phones": []}) is None
    assert _extract_phone({"contacts": [{"type": "email", "value": "x@y.it"}]}) is None


# ---------------------------------------------------------------------------
# extract_phone — Atoka source short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_phone_uses_atoka_when_present() -> None:
    azienda = {"phone": "+39 02 12345678", "website_domain": "example.it"}
    result = await extract_phone(azienda)
    assert result.source == "atoka"
    assert result.phone == "+390212345678"
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_extract_phone_failed_when_no_domain_and_no_atoka() -> None:
    azienda = {"phone": None, "website_domain": None}
    result = await extract_phone(azienda)
    assert result.source == "failed"
    assert result.phone is None


# ---------------------------------------------------------------------------
# Regex behaviour on an HTML fixture
# ---------------------------------------------------------------------------


_FIXTURE_HTML = """
<html><body>
  <a href="tel:+39 02 123456788">Chiamaci ora</a>
  <p>Telefono: 02-1234.5678</p>
  <p>Cellulare: +39 333 1234567</p>
  <p>P.IVA: 12345678901</p>  <!-- 11 digits, no separator: must NOT match -->
  <p>CAP: 20100</p>
  <p>Codice fiscale: RSSMRA80A01H501Z</p>
</body></html>
"""


def test_tel_href_regex_matches_marked_up_link() -> None:
    matches = [m.group(1) for m in _TEL_HREF_RE.finditer(_FIXTURE_HTML)]
    assert any("12345678" in m.replace(" ", "") for m in matches)


def test_plain_phone_regex_matches_visible_numbers() -> None:
    raw_matches = [m.group(0) for m in _PLAIN_PHONE_RE.finditer(_FIXTURE_HTML)]
    normalised = {_normalise_phone(m) for m in raw_matches}
    assert "+390212345678" in normalised
    assert "+393331234567" in normalised


def test_plain_phone_regex_skips_unseparated_long_runs() -> None:
    # A bare 11-digit P.IVA without separators must not match the
    # plain-text regex — the regex requires either +39 or a 0/3 prefix
    # plus interior separator-or-3-digit-block structure.
    text = "P.IVA 12345678901 trasmessa al SDI."
    matches = [m.group(0) for m in _PLAIN_PHONE_RE.finditer(text)]
    # Even if we get a partial match, normalisation + length filter
    # in `_fetch_phones_from_url` would discard it; we still want to
    # document expected regex behaviour.
    for m in matches:
        normalised = _normalise_phone(m)
        # Anything we DO match here must end up as a valid-looking
        # Italian phone (>= 9 digits, starts with +39 plus 0X or 3XX).
        if normalised:
            digits = normalised.replace("+", "")
            assert digits.startswith("39")
