"""Tests for `contact_extractor` (ADR-003).

The wrapper sits over `email_extractor.extract_email` and broadens the
output channel from "email-only" to one of {email, whatsapp, phone_only}.
The cascade order is load-bearing — every reroute decision the
orchestrator makes downstream depends on the wrapper picking the right
branch first.

These tests pin:
  • The four-step priority cascade (Atoka email > Atoka WA > scrape > phone).
  • The performance contract: when the Atoka email branch hits, the
    wrapper does NOT delegate to `extract_email` (no wasted scrape).
  • WhatsApp number normalisation rejecting fixed-line / malformed input.
  • The blacklist short-circuit returning a `failed` result instead of
    silently routing to WhatsApp / phone (a blacklist applies cross-channel).
  • The exhausted-cascade `failed` sentinel preserves the audit-log shape.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.services import contact_extractor as ce
from src.services.contact_extractor import (
    ContactResult,
    _normalise_wa_number,
    _probe_whatsapp,
    extract_contact,
)
from src.services.email_extractor import ExtractionResult


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeSb:
    """Minimal stub for `extract_email`'s `sb` argument.

    `_check_blacklists` only touches `sb` when an email is found and it
    needs to query the blacklist tables. Because we monkeypatch
    `_check_blacklists` directly in these tests, the stub never sees a
    real call — but `extract_email` still needs *some* truthy value to
    pass through.
    """

    def table(self, name: str) -> Any:  # pragma: no cover — never called
        raise AssertionError(f"sb.table({name!r}) should not be invoked")


@pytest.fixture
def sb() -> _FakeSb:
    return _FakeSb()


@pytest.fixture
def patch_blacklist_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force `_check_blacklists` to return `None` (= clean)."""

    async def _ok(email: str, *, sb: Any) -> None:
        return None

    monkeypatch.setattr(ce, "_check_blacklists", _ok)


@pytest.fixture
def patch_blacklist_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force `_check_blacklists` to return a failed ExtractionResult."""

    async def _bad(email: str, *, sb: Any) -> ExtractionResult:
        return ExtractionResult(
            email=None,
            source="failed",
            confidence=0.0,
            cost_cents=0,
            company_name=None,
            domain=None,
            notes=f"blacklisted:{email}",
        )

    monkeypatch.setattr(ce, "_check_blacklists", _bad)


@pytest.fixture
def block_extract_email(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Make `extract_email` raise if called.

    Used by tests that assert the dominant Atoka-email path short-
    circuits *before* the website scraper runs (the performance contract
    of this wrapper). Returns a list mutated to `[True]` if invoked, but
    asserting non-invocation is the primary guarantee.
    """

    called: list[bool] = []

    async def _explode(*args: Any, **kwargs: Any) -> Any:
        called.append(True)
        raise AssertionError(
            "extract_email must not be called when the Atoka cascade "
            "short-circuits — see ADR-003 performance contract."
        )

    monkeypatch.setattr(ce, "extract_email", _explode)
    return called


# ---------------------------------------------------------------------------
# Branch 1 — Atoka email (the 85% happy path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_atoka_email_short_circuits_without_invoking_extract_email(
    sb: _FakeSb,
    patch_blacklist_clean: None,
    block_extract_email: list[bool],
) -> None:
    """The dominant case must NOT touch the website scraper. This is
    the core performance contract from ADR-003 — a regression here
    would silently 5-second-stall every email-bearing lead."""
    azienda = {
        "email": "marco.rossi@acmesrl.it",
        "legal_name": "ACME SRL",
        "website_domain": "acmesrl.it",
    }
    result = await extract_contact(azienda, sb=sb)

    assert result.channel == "email"
    assert result.value == "marco.rossi@acmesrl.it"
    assert result.source == "atoka"
    assert result.confidence == 1.0
    assert result.cost_cents == 0
    assert block_extract_email == []  # extract_email was not called


@pytest.mark.asyncio
async def test_atoka_role_account_falls_through_to_next_branch(
    sb: _FakeSb,
    patch_blacklist_clean: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`info@` etc. are filtered out at the Atoka step. Without WA or
    a phone, the wrapper should delegate to `extract_email` (which will
    try the website scrape) — proving the role-account exclusion is
    inherited correctly from the underlying extractor."""

    async def _scrape_ok(*args: Any, **kwargs: Any) -> ExtractionResult:
        return ExtractionResult(
            email="ceo@acmesrl.it",
            source="website_scrape",
            confidence=0.8,
            cost_cents=0,
            company_name="ACME SRL",
            domain="acmesrl.it",
            notes="scraped",
        )

    monkeypatch.setattr(ce, "extract_email", _scrape_ok)
    azienda = {
        "email": "info@acmesrl.it",  # role account → skipped
        "legal_name": "ACME SRL",
        "website_domain": "acmesrl.it",
    }
    result = await extract_contact(azienda, sb=sb)

    assert result.channel == "email"
    assert result.source == "website_scrape"
    assert result.value == "ceo@acmesrl.it"


@pytest.mark.asyncio
async def test_blacklisted_email_does_not_route_to_whatsapp(
    sb: _FakeSb,
    patch_blacklist_hit: None,
    block_extract_email: list[bool],
) -> None:
    """A blacklist hit is a hard cross-channel stop — we must NOT silently
    divert the lead to WhatsApp just because the email branch failed.
    The audit log needs to show the email was blacklisted, not that we
    found a 'good' WhatsApp instead."""
    azienda = {
        "email": "marco.rossi@acmesrl.it",
        "whatsapp": "+39 333 1234567",  # would otherwise win at branch 2
        "phone": "+39 333 1234567",
        "legal_name": "ACME SRL",
        "website_domain": "acmesrl.it",
    }
    result = await extract_contact(azienda, sb=sb)

    assert result.channel == "email"
    assert result.value is None
    assert result.source == "failed"
    assert "blacklisted" in result.notes
    assert block_extract_email == []


# ---------------------------------------------------------------------------
# Branch 2 — Atoka WhatsApp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_atoka_whatsapp_preempts_website_scrape(
    sb: _FakeSb,
    patch_blacklist_clean: None,
    block_extract_email: list[bool],
) -> None:
    """Per ADR-003, Atoka WA outranks scraping — so the wrapper must
    NOT call extract_email when WA is available. Saves the 5-second
    HTTP timeout budget on the rows where WA is the right answer."""
    azienda = {
        "email": None,
        "whatsapp": "+39 333 1234567",
        "phone": "+39 02 12345678",  # would also satisfy phone_only
        "legal_name": "ACME SRL",
        "website_domain": "acmesrl.it",
    }
    result = await extract_contact(azienda, sb=sb)

    assert result.channel == "whatsapp"
    assert result.value == "+393331234567"
    assert result.source == "atoka"
    assert result.confidence == 1.0
    assert block_extract_email == []


@pytest.mark.asyncio
async def test_whatsapp_extracted_from_contacts_list(
    sb: _FakeSb,
    patch_blacklist_clean: None,
    block_extract_email: list[bool],
) -> None:
    """Atoka raw payloads sometimes carry a typed contacts list."""
    azienda = {
        "email": None,
        "contacts": [
            {"type": "phone", "value": "02 12345678"},
            {"type": "whatsapp", "value": "+39-333-123-4567"},
        ],
        "legal_name": "ACME SRL",
    }
    result = await extract_contact(azienda, sb=sb)

    assert result.channel == "whatsapp"
    assert result.value == "+393331234567"


@pytest.mark.asyncio
async def test_whatsapp_rejects_fixed_line_falls_through(
    sb: _FakeSb,
    patch_blacklist_clean: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A landline in the WA field would just produce a Meta send-failure;
    treat it as no-WA and continue the cascade."""

    async def _scrape_failed(*args: Any, **kwargs: Any) -> ExtractionResult:
        return ExtractionResult(
            email=None,
            source="failed",
            confidence=0.0,
            cost_cents=0,
            notes="no website",
        )

    monkeypatch.setattr(ce, "extract_email", _scrape_failed)
    azienda = {
        "email": None,
        "whatsapp": "+39 02 12345678",  # landline — invalid for WA
        "phone": "+39 02 12345678",
        "legal_name": "ACME SRL",
    }
    result = await extract_contact(azienda, sb=sb)

    assert result.channel == "phone_only"  # WA rejected → phone fallback
    assert result.value == "+39 02 12345678"


# ---------------------------------------------------------------------------
# Branch 3 — Website scrape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_website_scrape_branch(
    sb: _FakeSb,
    patch_blacklist_clean: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _scrape_ok(*args: Any, **kwargs: Any) -> ExtractionResult:
        return ExtractionResult(
            email="contatti@acmesrl.it",
            source="website_scrape",
            confidence=0.7,
            cost_cents=0,
            company_name="ACME SRL",
            domain="acmesrl.it",
            raw_response={"page_url": "https://acmesrl.it/contatti"},
            notes="scraped",
        )

    monkeypatch.setattr(ce, "extract_email", _scrape_ok)
    azienda = {
        "email": None,
        "legal_name": "ACME SRL",
        "website_domain": "acmesrl.it",
    }
    result = await extract_contact(azienda, sb=sb)

    assert result.channel == "email"
    assert result.source == "website_scrape"
    assert result.value == "contatti@acmesrl.it"
    assert result.confidence == 0.7
    assert result.raw == {"page_url": "https://acmesrl.it/contatti"}


# ---------------------------------------------------------------------------
# Branch 4 — phone-only fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phone_only_fallback_when_everything_else_fails(
    sb: _FakeSb,
    patch_blacklist_clean: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _scrape_failed(*args: Any, **kwargs: Any) -> ExtractionResult:
        return ExtractionResult(
            email=None,
            source="failed",
            confidence=0.0,
            cost_cents=0,
            notes="no website",
        )

    monkeypatch.setattr(ce, "extract_email", _scrape_failed)
    azienda = {
        "email": None,
        "phone": "+39 02 12345678",
        "legal_name": "ACME SRL",
    }
    result = await extract_contact(azienda, sb=sb)

    assert result.channel == "phone_only"
    assert result.value == "+39 02 12345678"
    assert result.source == "atoka"
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_cascade_exhausted_returns_failed_sentinel(
    sb: _FakeSb,
    patch_blacklist_clean: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _scrape_failed(*args: Any, **kwargs: Any) -> ExtractionResult:
        return ExtractionResult(
            email=None,
            source="failed",
            confidence=0.0,
            cost_cents=0,
            notes="no website",
        )

    monkeypatch.setattr(ce, "extract_email", _scrape_failed)
    azienda = {
        "email": None,
        "phone": None,
        "legal_name": "ACME SRL",
        "website_domain": None,
    }
    result = await extract_contact(azienda, sb=sb)

    # Failed sentinel keeps the historic 'email' channel so downstream
    # log queries that filter `WHERE channel='email' AND source='failed'`
    # continue to surface this row.
    assert result.channel == "email"
    assert result.value is None
    assert result.source == "failed"
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# WhatsApp number normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+39 333 123 4567", "+393331234567"),
        ("39 333 1234567", "+393331234567"),
        ("0039 333 1234567", "+393331234567"),
        ("3331234567", "+393331234567"),
        ("+39-333-123-4567", "+393331234567"),
        ("(+39) 333 1234567", "+393331234567"),
    ],
)
def test_normalise_wa_number_accepts_italian_mobile(raw: str, expected: str) -> None:
    assert _normalise_wa_number(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "abc",
        "02 12345678",        # fixed-line — not a mobile
        "+39 02 12345678",    # fixed-line with +39 — not a mobile
        "1234",               # too short
        "+1 555 1234567",     # US — only IT supported in Sprint 1
    ],
)
def test_normalise_wa_number_rejects_invalid(raw: str) -> None:
    assert _normalise_wa_number(raw) is None


# ---------------------------------------------------------------------------
# _probe_whatsapp — direct unit coverage
# ---------------------------------------------------------------------------


def test_probe_whatsapp_top_level_keys() -> None:
    for key in ("whatsapp", "whatsapp_phone", "whatsapp_number", "wa_phone"):
        assert _probe_whatsapp({key: "+39 333 1234567"}) == "+393331234567"


def test_probe_whatsapp_returns_none_when_absent() -> None:
    assert _probe_whatsapp({}) is None
    assert _probe_whatsapp({"phone": "+39 333 1234567"}) is None  # phone ≠ WA
    assert _probe_whatsapp({"whatsapp": ""}) is None


def test_probe_whatsapp_skips_non_dict_contacts() -> None:
    """Defensive: malformed Atoka payloads (`contacts: ["string"]`)
    must not raise — the wrapper would silently fail every lead."""
    assert _probe_whatsapp({"contacts": ["bogus", 42, None]}) is None


# ---------------------------------------------------------------------------
# ContactResult basics
# ---------------------------------------------------------------------------


def test_contact_result_is_frozen() -> None:
    """Frozen dataclass — accidentally mutating one lead's contact while
    iterating over a batch is an easy bug to ship."""
    r = ContactResult(channel="email", value="x@y.it", source="atoka", confidence=1.0)
    with pytest.raises(Exception):  # FrozenInstanceError, but stdlib raises in stricter form
        r.value = "z@y.it"  # type: ignore[misc]


def test_contact_result_failed_factory() -> None:
    r = ContactResult.failed(
        company_name="ACME SRL",
        domain="acmesrl.it",
        notes="no channel",
    )
    assert r.channel == "email"
    assert r.value is None
    assert r.source == "failed"
    assert r.company_name == "ACME SRL"
