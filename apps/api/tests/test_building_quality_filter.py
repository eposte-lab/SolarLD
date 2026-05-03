"""Tests for building_quality_filter — pure heuristic logic."""

from __future__ import annotations

from src.services import building_quality_filter as bqf


def test_full_signals_passes_with_max_score() -> None:
    out = bqf.passes_filter_simple(
        user_ratings_total=12,
        website="https://acme.it",
        phone="+39 030 1234567",
        business_status="OPERATIONAL",
    )
    assert out.score == 5
    assert out.passed is True
    assert "ratings>=5" in out.reasons
    assert "website_present" in out.reasons
    assert "phone_present" in out.reasons
    assert "business_operational" in out.reasons


def test_only_website_does_not_reach_threshold() -> None:
    out = bqf.passes_filter_simple(
        user_ratings_total=None,
        website="https://acme.it",
        phone=None,
        business_status=None,
    )
    # 0 + 2 + 0 + 0 = 2, below threshold (3)
    assert out.score == 2
    assert out.passed is False


def test_website_plus_phone_passes() -> None:
    out = bqf.passes_filter_simple(
        user_ratings_total=None,
        website="https://acme.it",
        phone="030-1234567",
        business_status=None,
    )
    # 0 + 2 + 1 + 0 = 3 → passes
    assert out.score == 3
    assert out.passed is True


def test_business_closed_loses_one_point() -> None:
    out = bqf.passes_filter_simple(
        user_ratings_total=10,
        website="https://acme.it",
        phone="+39 030 1234567",
        business_status="CLOSED_TEMPORARILY",
    )
    # 1 + 2 + 1 + 0 = 4
    assert out.score == 4
    assert out.passed is True


def test_zero_signals_fails() -> None:
    out = bqf.passes_filter_simple(
        user_ratings_total=0,
        website=None,
        phone=None,
        business_status=None,
    )
    assert out.score == 0
    assert out.passed is False
    assert out.reasons == []


def test_few_ratings_count_below_threshold() -> None:
    out = bqf.passes_filter_simple(
        user_ratings_total=3,
        website="https://acme.it",
        phone=None,
        business_status="OPERATIONAL",
    )
    # 0 + 2 + 0 + 1 = 3 → passes
    assert out.score == 3
    assert out.passed is True


def test_min_area_table_present_for_known_sectors() -> None:
    """The CV-future table must keep coverage for the wizard_groups
    we actually seed in ateco_google_types so the prompt context can
    inject the threshold per candidate."""
    expected_keys = {
        "industry_heavy",
        "industry_light",
        "food_production",
        "logistics",
        "retail_gdo",
        "hospitality_large",
        "healthcare",
        "agricultural_intensive",
    }
    assert expected_keys.issubset(set(bqf.MIN_BUILDING_AREA_M2_BY_SECTOR.keys()))
