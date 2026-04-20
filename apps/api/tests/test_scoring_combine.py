"""Unit tests for the breakdown → weighted score → tier pipeline."""

from __future__ import annotations

from src.models.enums import LeadScoreTier
from src.services.scoring import (
    ScoringBreakdown,
    ScoringWeights,
    combine_breakdown,
    tier_for,
)


def _br(
    technical: int = 0,
    consumption: int = 0,
    incentives: int = 0,
    solvency: int = 0,
    distance: int = 0,
) -> ScoringBreakdown:
    return ScoringBreakdown(
        technical=technical,
        consumption=consumption,
        incentives=incentives,
        solvency=solvency,
        distance=distance,
    )


def test_weights_from_jsonb_uses_default_when_missing() -> None:
    w = ScoringWeights.from_jsonb(None)
    assert w.technical == 25.0
    assert w.consumption == 25.0
    assert w.incentives == 15.0
    assert w.solvency == 20.0
    assert w.distance == 15.0
    assert w.total() == 100.0


def test_weights_from_jsonb_respects_override() -> None:
    w = ScoringWeights.from_jsonb(
        {"technical": 40, "consumption": 20, "incentives": 10, "solvency": 20, "distance": 10}
    )
    assert w.technical == 40.0
    assert w.total() == 100.0


def test_combine_all_100_gives_100() -> None:
    w = ScoringWeights.from_jsonb(None)
    score = combine_breakdown(
        _br(technical=100, consumption=100, incentives=100, solvency=100, distance=100),
        w,
    )
    assert score == 100


def test_combine_all_zero_gives_zero() -> None:
    w = ScoringWeights.from_jsonb(None)
    assert combine_breakdown(_br(), w) == 0


def test_combine_weighted_average_example() -> None:
    # With default weights (25/25/15/20/15):
    # technical=80, consumption=60, incentives=50, solvency=60, distance=70
    # = 80*25 + 60*25 + 50*15 + 60*20 + 70*15 = 2000+1500+750+1200+1050 = 6500/100 = 65
    w = ScoringWeights.from_jsonb(None)
    score = combine_breakdown(
        _br(technical=80, consumption=60, incentives=50, solvency=60, distance=70),
        w,
    )
    assert score == 65


def test_combine_normalizes_when_weights_dont_sum_to_100() -> None:
    w = ScoringWeights.from_jsonb(
        {"technical": 50, "consumption": 50, "incentives": 50, "solvency": 50, "distance": 50}
    )
    # Sum is 250 but output still within 0..100
    score = combine_breakdown(_br(technical=100, consumption=100), w)
    # weighted avg = (100*50 + 100*50) / 250 = 40
    assert score == 40


def test_combine_zero_weights_falls_back_to_equal_avg() -> None:
    w = ScoringWeights(0.0, 0.0, 0.0, 0.0, 0.0)
    score = combine_breakdown(_br(technical=100, consumption=50), w)
    # plain average = (100+50)/5 = 30
    assert score == 30


def test_tier_thresholds() -> None:
    assert tier_for(100) is LeadScoreTier.HOT
    assert tier_for(76) is LeadScoreTier.HOT
    assert tier_for(75) is LeadScoreTier.WARM
    assert tier_for(60) is LeadScoreTier.WARM
    assert tier_for(59) is LeadScoreTier.COLD
    assert tier_for(40) is LeadScoreTier.COLD
    assert tier_for(39) is LeadScoreTier.REJECTED
    assert tier_for(0) is LeadScoreTier.REJECTED


def test_tier_with_min_threshold_rejects_below_floor() -> None:
    # Elite setting (threshold 85) — a score that would normally be
    # HOT (76..) must still pass the floor.
    assert tier_for(84, min_threshold=85) is LeadScoreTier.REJECTED
    assert tier_for(85, min_threshold=85) is LeadScoreTier.HOT


def test_tier_with_min_threshold_collapses_cold_to_rejected() -> None:
    # Equilibrato (60): COLD (40..59) leads should all become REJECTED.
    assert tier_for(59, min_threshold=60) is LeadScoreTier.REJECTED
    assert tier_for(40, min_threshold=60) is LeadScoreTier.REJECTED
    assert tier_for(60, min_threshold=60) is LeadScoreTier.WARM


def test_tier_with_min_threshold_aggressive_preserves_cold() -> None:
    # Aggressivo (40) matches the PRD default — COLD tier stays COLD,
    # only sub-40 rejects.
    assert tier_for(40, min_threshold=40) is LeadScoreTier.COLD
    assert tier_for(39, min_threshold=40) is LeadScoreTier.REJECTED


def test_tier_with_min_threshold_none_keeps_legacy_behaviour() -> None:
    # Passing None is the explicit opt-out for legacy callers + tests.
    assert tier_for(50, min_threshold=None) is LeadScoreTier.COLD
    assert tier_for(50) is LeadScoreTier.COLD  # default arg


def test_breakdown_to_dict_roundtrip() -> None:
    br = _br(technical=10, consumption=20, incentives=30, solvency=40, distance=50)
    d = br.to_dict()
    assert d == {
        "technical": 10,
        "consumption": 20,
        "incentives": 30,
        "solvency": 40,
        "distance": 50,
    }


def test_combine_clamps_within_0_100() -> None:
    # Paranoia: even with malformed huge weights, clamp to the valid range
    w = ScoringWeights.from_jsonb(
        {"technical": 1, "consumption": 0, "incentives": 0, "solvency": 0, "distance": 0}
    )
    score = combine_breakdown(_br(technical=120), w)
    # Shouldn't exceed 100 even if technical subscore was mis-fed
    assert 0 <= score <= 100
