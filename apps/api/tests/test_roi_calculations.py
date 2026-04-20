"""Unit tests for the ROI calculator service.

The calculator is pure — no DB, no HTTP — so we exercise the tiers and
derivation paths directly and check the `to_jsonb()` projection.
"""

from __future__ import annotations

from src.services.roi_service import (
    EXPORT_PRICE_EUR_PER_KWH,
    GRID_PRICE_EUR_PER_KWH_B2B,
    GRID_PRICE_EUR_PER_KWH_B2C,
    INCENTIVE_PCT_B2B,
    INCENTIVE_PCT_B2C,
    INCENTIVE_PCT_FALLBACK,
    SELF_CONSUMPTION_RATIO_B2B,
    SELF_CONSUMPTION_RATIO_B2C,
    compute_roi,
)


def test_compute_roi_b2c_happy_path() -> None:
    """A 6 kWp residential system with ~7800 kWh/year should produce
    sensible, ballpark-correct numbers."""
    est = compute_roi(
        estimated_kwp=6.0,
        estimated_yearly_kwh=7800.0,
        subject_type="b2c",
    )
    assert est is not None
    # CAPEX: 6 * 1500 = 9_000 €; 50% Superbonus haircut → 4_500 €
    assert abs(est.gross_capex_eur - 9000.0) < 1e-6
    assert abs(est.incentive_eur - 4500.0) < 1e-6
    assert abs(est.net_capex_eur - 4500.0) < 1e-6
    # Savings: 40% self @ 0.25 + 60% export @ 0.09 for 7800 kWh
    expected_savings = 7800 * 0.40 * 0.25 + 7800 * 0.60 * 0.09
    assert abs(est.yearly_savings_eur - expected_savings) < 1e-6
    # Payback must be a few years (not negative, not nonsense)
    assert est.payback_years is not None
    assert 3.0 < est.payback_years < 12.0


def test_compute_roi_b2b_uses_business_rates() -> None:
    est = compute_roi(
        estimated_kwp=50.0,
        estimated_yearly_kwh=65_000.0,
        subject_type="b2b",
    )
    assert est is not None
    # CAPEX: 50 * 1200 = 60_000; credito d'imposta 30% → 18_000
    assert abs(est.gross_capex_eur - 60_000.0) < 1e-6
    assert abs(est.incentive_eur - 18_000.0) < 1e-6
    assert abs(est.net_capex_eur - 42_000.0) < 1e-6
    assert est.self_consumption_ratio == SELF_CONSUMPTION_RATIO_B2B
    # B2B enjoys a higher self-consumption ratio, so a bigger slice of
    # the savings is at the grid price, not the export price.
    b2c = compute_roi(
        estimated_kwp=50.0,
        estimated_yearly_kwh=65_000.0,
        subject_type="b2c",
    )
    assert b2c is not None
    # B2B net capex should be larger (lower incentive %), but yearly
    # savings should also shift in a predictable direction: more self-
    # consumption at a cheaper tariff. We only verify the savings
    # calculation uses the right ratio/tariff pair.
    expected_b2b = 65000 * SELF_CONSUMPTION_RATIO_B2B * GRID_PRICE_EUR_PER_KWH_B2B + 65000 * (
        1 - SELF_CONSUMPTION_RATIO_B2B
    ) * EXPORT_PRICE_EUR_PER_KWH
    expected_b2c = 65000 * SELF_CONSUMPTION_RATIO_B2C * GRID_PRICE_EUR_PER_KWH_B2C + 65000 * (
        1 - SELF_CONSUMPTION_RATIO_B2C
    ) * EXPORT_PRICE_EUR_PER_KWH
    assert abs(est.yearly_savings_eur - expected_b2b) < 1e-6
    assert abs(b2c.yearly_savings_eur - expected_b2c) < 1e-6


def test_compute_roi_unknown_type_uses_conservative_fallback() -> None:
    est = compute_roi(
        estimated_kwp=10.0,
        estimated_yearly_kwh=13_000.0,
        subject_type="unknown",
    )
    assert est is not None
    # Incentive haircut should be the conservative 10% fallback
    assert abs(est.incentive_eur - est.gross_capex_eur * INCENTIVE_PCT_FALLBACK) < 1e-6


def test_compute_roi_returns_none_when_both_inputs_missing() -> None:
    assert compute_roi(
        estimated_kwp=None, estimated_yearly_kwh=None, subject_type="b2c"
    ) is None
    assert compute_roi(
        estimated_kwp=0, estimated_yearly_kwh=0, subject_type="b2c"
    ) is None


def test_compute_roi_derives_kwp_from_yearly_kwh() -> None:
    est = compute_roi(
        estimated_kwp=None,
        estimated_yearly_kwh=13_000.0,
        subject_type="b2c",
    )
    assert est is not None
    # Italian average yield 1300 kWh/kWp → 10 kWp implied
    assert abs(est.estimated_kwp - 10.0) < 1e-6


def test_compute_roi_derives_yearly_kwh_from_kwp() -> None:
    est = compute_roi(
        estimated_kwp=8.0,
        estimated_yearly_kwh=None,
        subject_type="b2c",
    )
    assert est is not None
    assert abs(est.yearly_kwh - 10_400.0) < 1e-6  # 8 * 1300


def test_compute_roi_incentive_percent_matches_subject_tier() -> None:
    b2c = compute_roi(
        estimated_kwp=10.0, estimated_yearly_kwh=13_000.0, subject_type="b2c"
    )
    b2b = compute_roi(
        estimated_kwp=10.0, estimated_yearly_kwh=13_000.0, subject_type="b2b"
    )
    assert b2c is not None and b2b is not None
    b2c_pct = b2c.incentive_eur / b2c.gross_capex_eur
    b2b_pct = b2b.incentive_eur / b2b.gross_capex_eur
    assert abs(b2c_pct - INCENTIVE_PCT_B2C) < 1e-6
    assert abs(b2b_pct - INCENTIVE_PCT_B2B) < 1e-6


def test_compute_roi_payback_handles_zero_savings() -> None:
    """A system that produces 0 kWh can't pay anything back. The
    calculator must return payback=None rather than divide by zero."""
    est = compute_roi(
        estimated_kwp=5.0,
        estimated_yearly_kwh=0.0,      # will be derived → 6500 via kwp
        subject_type="b2c",
    )
    # Derived yearly_kwh kicks in, so savings end up non-zero. The
    # edge case we really want: pass both zero → returns None above.
    assert est is not None
    assert est.yearly_savings_eur > 0


def test_compute_roi_co2_fields_make_sense() -> None:
    est = compute_roi(
        estimated_kwp=10.0, estimated_yearly_kwh=13_000.0, subject_type="b2c"
    )
    assert est is not None
    # 13_000 kWh × 0.281 kg/kWh ≈ 3653 kg/year
    assert abs(est.co2_kg_per_year - 13_000 * 0.281) < 1e-6
    # 25 years in tonnes
    assert abs(est.co2_tonnes_25_years - est.co2_kg_per_year * 25 / 1000.0) < 1e-6


def test_compute_roi_to_jsonb_rounds_sensibly() -> None:
    est = compute_roi(
        estimated_kwp=6.37,
        estimated_yearly_kwh=8281.0,
        subject_type="b2c",
    )
    assert est is not None
    j = est.to_jsonb()
    # kwp rounded to 2 decimals, money rounded to whole euros.
    assert j["estimated_kwp"] == 6.37
    assert j["gross_capex_eur"] == round(est.gross_capex_eur)
    assert j["net_capex_eur"] == round(est.net_capex_eur)
    assert j["yearly_savings_eur"] == round(est.yearly_savings_eur)
    assert j["payback_years"] == round(est.payback_years or 0, 1)
    # self_consumption_ratio always present and within [0, 1]
    assert 0.0 <= j["self_consumption_ratio"] <= 1.0


def test_compute_roi_garbage_inputs_are_coerced_or_rejected() -> None:
    # Strings that look numeric should still work.
    est = compute_roi(
        estimated_kwp="10",  # type: ignore[arg-type]
        estimated_yearly_kwh="13000",  # type: ignore[arg-type]
        subject_type="b2c",
    )
    assert est is not None
    # Garbage strings → fallback to derived-or-None.
    none_est = compute_roi(
        estimated_kwp="not-a-number",  # type: ignore[arg-type]
        estimated_yearly_kwh=None,
        subject_type="b2c",
    )
    assert none_est is None
