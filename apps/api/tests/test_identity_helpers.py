"""Tests for the pure helpers inside the Identity agent.

These functions don't touch the DB, HTTP, or the agent base — they only
operate on `VisuraOwner` / `AtokaProfile` dataclasses + lists of strings.
"""

from __future__ import annotations

from src.agents.identity import (
    _build_subject_row,
    _compute_pii_hash,
    _confidence_score,
    _sha256_normalized,
)
from src.models.enums import SubjectType
from src.services.italian_business_service import AtokaProfile, VisuraOwner


# ----- confidence score -----


def test_confidence_all_sources_present() -> None:
    score = _confidence_score(
        ["visura", "atoka", "hunter_io", "neverbounce"], email_verified=True
    )
    assert score == 1.0


def test_confidence_visura_only() -> None:
    assert _confidence_score(["visura"], email_verified=False) == 0.35


def test_confidence_nothing() -> None:
    assert _confidence_score([], email_verified=False) == 0.0


def test_confidence_caps_at_one() -> None:
    # Hypothetical over-count
    score = _confidence_score(
        ["visura", "atoka", "hunter_io", "neverbounce", "visura"],
        email_verified=True,
    )
    assert score == 1.0


# ----- pii_hash -----


def test_pii_hash_b2b_prefers_business_name_vat() -> None:
    visura = VisuraOwner(
        classification=SubjectType.B2B,
        business_name="Acme Srl",
        vat_number="IT12345678901",
    )
    h = _compute_pii_hash(visura=visura, atoka=None, fallback_city=None, fallback_cap=None)
    expected = _sha256_normalized("Acme Srl|IT12345678901")
    assert h == expected


def test_pii_hash_b2b_falls_back_to_atoka_when_visura_missing_name() -> None:
    atoka = AtokaProfile(
        vat_number="IT99999999999",
        legal_name="Beta SpA",
        ateco_code=None,
        ateco_description=None,
        yearly_revenue_cents=None,
        employees=None,
        website_domain=None,
        decision_maker_name=None,
        decision_maker_role=None,
        linkedin_url=None,
    )
    h = _compute_pii_hash(visura=None, atoka=atoka, fallback_city=None, fallback_cap=None)
    assert h == _sha256_normalized("Beta SpA|IT99999999999")


def test_pii_hash_b2c_uses_name_address() -> None:
    visura = VisuraOwner(
        classification=SubjectType.B2C,
        owner_first_name="Mario",
        owner_last_name="Rossi",
        postal_address="Via Roma 1",
        postal_cap="80100",
        postal_city="Napoli",
    )
    h = _compute_pii_hash(visura=visura, atoka=None, fallback_city=None, fallback_cap=None)
    assert h == _sha256_normalized("Mario Rossi|Via Roma 1|80100|Napoli")


def test_pii_hash_is_case_and_accent_insensitive() -> None:
    a = _sha256_normalized("Caffè Roma|IT12345")
    b = _sha256_normalized("CAFFÈ ROMA|IT12345")
    assert a == b


def test_pii_hash_fallback_uses_locality_marker() -> None:
    h = _compute_pii_hash(
        visura=None, atoka=None, fallback_city="Napoli", fallback_cap="80100"
    )
    assert h == _sha256_normalized("anon|80100|Napoli")


# ----- subject row builder -----


def test_build_row_b2b_merges_visura_and_atoka() -> None:
    visura = VisuraOwner(
        classification=SubjectType.B2B,
        business_name="Acme Srl",
        vat_number="IT12345",
        postal_address="Via Napoli 12",
        postal_cap="80100",
        postal_city="Napoli",
        postal_province="NA",
    )
    atoka = AtokaProfile(
        vat_number="IT12345",
        legal_name="Acme Srl Unipersonale",
        ateco_code="43.21.01",
        ateco_description="Installazione impianti elettrici",
        yearly_revenue_cents=50_000_00,
        employees=12,
        website_domain="acmesrl.it",
        decision_maker_name="Luca Bianchi",
        decision_maker_role="CEO",
        linkedin_url="https://linkedin.com/in/lucabianchi",
    )

    class _FakeEmail:
        email = "luca.bianchi@acmesrl.it"

    row = _build_subject_row(
        tenant_id="tid",
        roof_id="rid",
        classification=SubjectType.B2B,
        visura=visura,
        atoka=atoka,
        email_result=_FakeEmail(),
        email_verified=True,
        pii_hash="deadbeef",
        data_sources=["visura", "atoka", "hunter_io", "neverbounce"],
        enrichment_cost_cents=51,
        fallback_address=None,
        fallback_cap=None,
        fallback_city=None,
        fallback_province=None,
    )
    # Atoka's legal_name wins over Visura when both present
    assert row["business_name"] == "Acme Srl Unipersonale"
    assert row["vat_number"] == "IT12345"
    assert row["ateco_code"] == "43.21.01"
    assert row["employees"] == 12
    assert row["decision_maker_email"] == "luca.bianchi@acmesrl.it"
    assert row["decision_maker_email_verified"] is True
    assert row["type"] == "b2b"
    assert row["pii_hash"] == "deadbeef"


def test_build_row_b2c_uses_visura_name() -> None:
    visura = VisuraOwner(
        classification=SubjectType.B2C,
        owner_first_name="Giulia",
        owner_last_name="Verdi",
        postal_address="Via Dante 3",
        postal_cap="80121",
        postal_city="Napoli",
        postal_province="NA",
    )
    row = _build_subject_row(
        tenant_id="tid",
        roof_id="rid",
        classification=SubjectType.B2C,
        visura=visura,
        atoka=None,
        email_result=None,
        email_verified=False,
        pii_hash="cafebabe",
        data_sources=["visura"],
        enrichment_cost_cents=25,
        fallback_address=None,
        fallback_cap=None,
        fallback_city=None,
        fallback_province=None,
    )
    assert row["type"] == "b2c"
    assert row["owner_first_name"] == "Giulia"
    assert row["owner_last_name"] == "Verdi"
    # B2C row should NOT have decision-maker fields
    assert "decision_maker_email" not in row
    assert "ateco_code" not in row
    assert row["postal_cap"] == "80121"


def test_build_row_falls_back_to_roof_address_when_visura_missing_fields() -> None:
    row = _build_subject_row(
        tenant_id="tid",
        roof_id="rid",
        classification=SubjectType.UNKNOWN,
        visura=None,
        atoka=None,
        email_result=None,
        email_verified=False,
        pii_hash="abc",
        data_sources=[],
        enrichment_cost_cents=0,
        fallback_address="Fallback Street 1",
        fallback_cap="00100",
        fallback_city="Roma",
        fallback_province="RM",
    )
    assert row["postal_address_line1"] == "Fallback Street 1"
    assert row["postal_cap"] == "00100"
    assert row["postal_city"] == "Roma"
