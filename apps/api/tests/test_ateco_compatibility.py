"""Tests for the post-Atoka ATECO compatibility filter (Sprint C.1).

The filter is a pure function over ``AtokaProfile`` lists — no DB,
no HTTP. We exercise it directly here (the live L1 path is covered
by funnel integration tests).
"""

from __future__ import annotations

from src.agents.hunter_funnel.level1_discovery import (
    _filter_by_ateco_compatibility,
    _is_ateco_compatible,
)
from src.services.italian_business_service import AtokaProfile


def _profile(vat: str, ateco: str | None) -> AtokaProfile:
    return AtokaProfile(
        vat_number=vat,
        legal_name=f"Co {vat}",
        ateco_code=ateco,
        ateco_description=None,
        yearly_revenue_cents=None,
        employees=None,
        website_domain=None,
        decision_maker_name=None,
        decision_maker_role=None,
        linkedin_url=None,
    )


# ---- _is_ateco_compatible ----


def test_is_ateco_compatible_exact_match() -> None:
    assert _is_ateco_compatible("25.11", {"25"}) is True


def test_is_ateco_compatible_dotted_form() -> None:
    assert _is_ateco_compatible("25.11.00", {"25"}) is True


def test_is_ateco_compatible_wrong_prefix() -> None:
    assert _is_ateco_compatible("84.11", {"25"}) is False


def test_is_ateco_compatible_none_input() -> None:
    assert _is_ateco_compatible(None, {"25"}) is False


def test_is_ateco_compatible_empty_string() -> None:
    assert _is_ateco_compatible("", {"25"}) is False


def test_is_ateco_compatible_multiple_prefixes() -> None:
    assert _is_ateco_compatible("52.10", {"25", "52", "10"}) is True


# ---- _filter_by_ateco_compatibility ----


def test_filter_keeps_only_matching_profiles() -> None:
    profiles = [
        _profile("IT01", "25.11"),    # matches industry_heavy
        _profile("IT02", "84.11"),    # PA — out
        _profile("IT03", "52.10"),    # logistics — matches
        _profile("IT04", "70.22"),    # consulting — out
    ]
    kept = _filter_by_ateco_compatibility(
        profiles=profiles,
        expected_whitelist=["25", "25.11", "52", "52.10"],
        scan_id="scan-1",
        tenant_id="tenant-1",
    )
    assert {p.vat_number for p in kept} == {"IT01", "IT03"}


def test_filter_returns_empty_when_no_matches() -> None:
    profiles = [_profile("IT01", "84.11"), _profile("IT02", "70.22")]
    kept = _filter_by_ateco_compatibility(
        profiles=profiles,
        expected_whitelist=["25", "52"],
        scan_id="scan-1",
        tenant_id="tenant-1",
    )
    assert kept == []


def test_filter_with_empty_whitelist_passes_through() -> None:
    """Safety: when expected_whitelist is empty (e.g. tenant relies only
    on target_wizard_groups but seed lookup failed), don't filter at all
    — we'd otherwise reject everything Atoka returned."""
    profiles = [_profile("IT01", "25.11"), _profile("IT02", "84.11")]
    kept = _filter_by_ateco_compatibility(
        profiles=profiles,
        expected_whitelist=[],
        scan_id="scan-1",
        tenant_id="tenant-1",
    )
    assert {p.vat_number for p in kept} == {"IT01", "IT02"}


def test_filter_handles_dotted_whitelist_codes() -> None:
    """Whitelist codes may include the full 4-digit form (e.g. '25.11')
    — the filter only uses the first segment."""
    profiles = [
        _profile("IT01", "25.11.00"),
        _profile("IT02", "25.99"),  # different sub-class but same prefix
        _profile("IT03", "26.20"),  # different prefix
    ]
    kept = _filter_by_ateco_compatibility(
        profiles=profiles,
        expected_whitelist=["25.11"],
        scan_id="scan-1",
        tenant_id="tenant-1",
    )
    assert {p.vat_number for p in kept} == {"IT01", "IT02"}


def test_filter_drops_profile_with_null_ateco() -> None:
    profiles = [_profile("IT01", None), _profile("IT02", "25.11")]
    kept = _filter_by_ateco_compatibility(
        profiles=profiles,
        expected_whitelist=["25"],
        scan_id="scan-1",
        tenant_id="tenant-1",
    )
    assert [p.vat_number for p in kept] == ["IT02"]
