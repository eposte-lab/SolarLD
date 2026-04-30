"""Tests for the centralised cost calculator (ADR-005).

Pure functions, no I/O — these run fast and pin the projection
numbers that ops sees in the dashboard. If a constant in
`cost_calculator.py` changes the breakdown changes, and these tests
should catch it before it ships to the dashboard.
"""

from __future__ import annotations

import math

import pytest

from src.services import cost_calculator as cc
from src.services.cost_calculator import (
    ATOKA_COST_PER_CALL_EUR,
    ATOKA_PURCHASE_CREDITS,
    ATOKA_PURCHASE_PRICE_EUR,
    ATOKA_PURCHASE_RUNWAY_WORKING_DAYS,
    MonthlyCostBreakdown,
    PIPELINE_SURVIVAL_RATE_V2,
    cost_constants_snapshot,
    estimate_atoka_runway_days,
    estimate_monthly_costs,
)


# ---------------------------------------------------------------------------
# Atoka pricing — pin the contract terms.
# ---------------------------------------------------------------------------


def test_atoka_unit_cost_matches_purchase_invoice() -> None:
    """ADR-005 fixes Atoka at €3000 / 8000 credits = €0.375 each.
    If this drifts, projections silently lie."""
    expected = ATOKA_PURCHASE_PRICE_EUR / ATOKA_PURCHASE_CREDITS
    assert ATOKA_COST_PER_CALL_EUR == pytest.approx(expected)
    assert ATOKA_COST_PER_CALL_EUR == pytest.approx(0.375)


def test_atoka_runway_default_matches_invoice() -> None:
    """8 000 credits should last the negotiated 44 working days."""
    days = estimate_atoka_runway_days(ATOKA_PURCHASE_CREDITS)
    assert days == pytest.approx(ATOKA_PURCHASE_RUNWAY_WORKING_DAYS)


def test_atoka_runway_custom_rate() -> None:
    """A heavier consumer burns through credits faster."""
    days = estimate_atoka_runway_days(8_000, daily_call_rate=400.0)
    assert days == pytest.approx(20.0)


def test_atoka_runway_zero_credits() -> None:
    """Empty wallet → 0 days, no exception. Ops uses this for alerts."""
    assert estimate_atoka_runway_days(0) == 0.0
    assert estimate_atoka_runway_days(-100) == 0.0


def test_atoka_runway_zero_rate_is_infinite() -> None:
    """A daily rate of zero (no spend) means credits never deplete."""
    assert math.isinf(estimate_atoka_runway_days(1, daily_call_rate=0.0))


# ---------------------------------------------------------------------------
# Monthly cost projection
# ---------------------------------------------------------------------------


def test_estimate_monthly_costs_returns_immutable_breakdown() -> None:
    """Frozen dataclass — accidentally mutating one tenant's
    projection while iterating over many is an easy bug to ship."""
    out = estimate_monthly_costs(100)
    assert isinstance(out, MonthlyCostBreakdown)
    with pytest.raises(AttributeError):
        out.atoka_eur = 0  # type: ignore[misc]


def test_estimate_monthly_costs_targets_use_22_working_days() -> None:
    out = estimate_monthly_costs(100)
    assert out.monthly_email_target == 100 * 22


def test_estimate_monthly_costs_atoka_calls_use_survival_rate() -> None:
    """At cap=100 / 22 days / 0.60 survival → 3667 calls / month."""
    out = estimate_monthly_costs(100)
    expected_calls = (100 * 22) / PIPELINE_SURVIVAL_RATE_V2
    assert out.atoka_calls == pytest.approx(expected_calls)
    assert out.atoka_eur == pytest.approx(expected_calls * 0.375)


def test_estimate_monthly_costs_per_email_cost_decreases_with_scale() -> None:
    """Fixed infra (€273/month) amortises across more emails as the
    cap rises — economies-of-scale curve must be monotonic."""
    small = estimate_monthly_costs(50)
    medium = estimate_monthly_costs(100)
    large = estimate_monthly_costs(200)
    assert (
        small.cost_per_delivered_email_eur
        > medium.cost_per_delivered_email_eur
        > large.cost_per_delivered_email_eur
    )


def test_estimate_monthly_costs_total_equals_sum_of_parts() -> None:
    """Pin the equation so a refactor that drops a line item gets
    caught immediately."""
    out = estimate_monthly_costs(100)
    parts = (
        out.atoka_eur
        + out.solar_eur
        + out.rendering_eur
        + out.validation_eur
        + out.email_send_eur
        + out.whatsapp_send_eur
        + out.infrastructure_eur
    )
    assert out.total_monthly_eur == pytest.approx(parts)


def test_estimate_monthly_costs_caps_scale_linearly() -> None:
    """Variable cost scales linearly with daily_cap; only fixed
    infra (€273/month) is invariant. Total at 2× cap should be
    *less* than 2× the smaller total (because fixed amortises) but
    *more* than the variable-only doubling.

    This catches order-of-magnitude regressions (e.g., dropping a
    × 22 working-days multiplier) without pinning the absolute
    euro value, which is fragile to per-line-item tweaks like
    rendering price.
    """
    cap_100 = estimate_monthly_costs(100).total_monthly_eur
    cap_200 = estimate_monthly_costs(200).total_monthly_eur

    # 2× the cap should produce more than the smaller total but less
    # than 2× — fixed infra amortises across more emails.
    assert cap_200 > cap_100, f"cap_200 ({cap_200}) ≤ cap_100 ({cap_100})"
    assert cap_200 < 2 * cap_100, (
        f"cap_200 ({cap_200}) ≥ 2× cap_100 ({2 * cap_100}) — fixed "
        "infra not amortising"
    )

    # Sanity: cap-100 lands in the low thousands €, not hundreds or
    # tens-of-thousands. Catches a missing × 22 (would give ~€130) or
    # an extra × 22 (would give ~€60k).
    assert 1_500 <= cap_100 <= 6_000, f"cap_100 wildly off: {cap_100}"


def test_estimate_monthly_costs_invalid_cap_raises() -> None:
    with pytest.raises(ValueError):
        estimate_monthly_costs(0)
    with pytest.raises(ValueError):
        estimate_monthly_costs(-10)


def test_estimate_monthly_costs_invalid_survival_raises() -> None:
    with pytest.raises(ValueError):
        estimate_monthly_costs(100, survival_rate=0.0)
    with pytest.raises(ValueError):
        estimate_monthly_costs(100, survival_rate=1.5)


def test_estimate_monthly_costs_invalid_offline_raises() -> None:
    with pytest.raises(ValueError):
        estimate_monthly_costs(100, offline_survival=-0.1)
    with pytest.raises(ValueError):
        estimate_monthly_costs(100, offline_survival=2.0)


def test_estimate_monthly_costs_invalid_working_days_raises() -> None:
    with pytest.raises(ValueError):
        estimate_monthly_costs(100, working_days=0)


def test_estimate_monthly_costs_as_dict_serialises_correctly() -> None:
    out = estimate_monthly_costs(100).as_dict()
    assert out["daily_cap"] == 100
    assert out["monthly_email_target"] == 2_200
    assert "variable" in out and "fixed" in out and "totals" in out
    # Numbers are rounded for display — sanity-check rounding precision.
    assert out["totals"]["cost_per_delivered_email_eur"] == round(
        out["totals"]["cost_per_delivered_email_eur"], 4
    )


# ---------------------------------------------------------------------------
# Snapshot — pin the constants the dashboard renders.
# ---------------------------------------------------------------------------


def test_cost_constants_snapshot_has_all_keys() -> None:
    snap = cost_constants_snapshot()
    # Pin exact key set so adding/removing a constant requires updating
    # this assertion deliberately.
    assert set(snap.keys()) == {
        "atoka_per_call_eur",
        "atoka_discovery_per_record_eur",
        "google_solar_building_insights_eur",
        "google_solar_data_layers_eur",
        "neverbounce_per_verify_eur",
        "resend_per_email_eur",
        "whatsapp_per_message_eur",
        "rendering_per_email_eur",
        "deliverability_infra_monthly_eur",
        "workspace_mailbox_monthly_eur",
        "dialog360_subscription_monthly_eur",
        "pipeline_survival_rate_v2",
        "offline_filters_survival_rate",
        "channel_mix_email",
        "channel_mix_whatsapp",
        "channel_mix_phone_only",
        "working_days_per_month",
        "atoka_purchase_credits",
        "atoka_purchase_price_eur",
        "atoka_purchase_runway_working_days",
    }


def test_channel_mix_shares_sum_to_one() -> None:
    """email + whatsapp + phone_only must partition the universe.
    Drift here would silently mis-allocate the per-channel cost
    breakdown."""
    total = (
        cc.CHANNEL_MIX_EMAIL
        + cc.CHANNEL_MIX_WHATSAPP
        + cc.CHANNEL_MIX_PHONE_ONLY
    )
    assert total == pytest.approx(1.0)
