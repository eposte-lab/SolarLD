"""Unit tests for the incentives subscore."""

from __future__ import annotations

from datetime import date, timedelta

from src.services.scoring import incentives_score


def _inc(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "target": "both",
        "deadline": None,
    }
    base.update(overrides)
    return base


TODAY = date(2026, 4, 16)


def test_no_incentives_baseline_20() -> None:
    assert incentives_score([], "b2b", today=TODAY) == 20


def test_single_matching_incentive_scores_50() -> None:
    assert incentives_score([_inc()], "b2b", today=TODAY) == 50


def test_two_matching_incentives_scores_75() -> None:
    assert incentives_score([_inc(), _inc()], "b2b", today=TODAY) == 75


def test_three_plus_matching_incentives_scores_90() -> None:
    score = incentives_score([_inc(), _inc(), _inc(), _inc()], "b2c", today=TODAY)
    assert score == 90


def test_target_filter_excludes_non_matching() -> None:
    # A B2B-only incentive should be invisible to a B2C subject
    b2b_only = _inc(target="b2b")
    b2c_only = _inc(target="b2c")
    assert incentives_score([b2b_only], "b2c", today=TODAY) == 20
    assert incentives_score([b2c_only], "b2b", today=TODAY) == 20


def test_both_target_matches_everyone() -> None:
    assert incentives_score([_inc(target="both")], "b2b", today=TODAY) == 50
    assert incentives_score([_inc(target="both")], "b2c", today=TODAY) == 50


def test_deadline_within_90_days_boosts_urgency() -> None:
    soon = _inc(deadline=(TODAY + timedelta(days=30)).isoformat())
    far = _inc(deadline=(TODAY + timedelta(days=400)).isoformat())
    urgent = incentives_score([soon], "b2b", today=TODAY)
    relaxed = incentives_score([far], "b2b", today=TODAY)
    assert urgent == 60  # 50 base + 10 urgency
    assert relaxed == 50


def test_urgency_bonus_caps_at_100() -> None:
    soon = _inc(deadline=(TODAY + timedelta(days=30)).isoformat())
    score = incentives_score([soon, soon, soon, soon], "b2b", today=TODAY)
    assert score == 100


def test_expired_deadline_no_bonus() -> None:
    past = _inc(deadline=(TODAY - timedelta(days=10)).isoformat())
    assert incentives_score([past], "b2b", today=TODAY) == 50


def test_handles_malformed_deadline_string() -> None:
    broken = _inc(deadline="not-a-date")
    # Should still count the incentive, just no urgency bonus
    assert incentives_score([broken], "b2b", today=TODAY) == 50


def test_unknown_target_defaults_to_both() -> None:
    # If target is None, treat as 'both'
    anon = {"target": None, "deadline": None}
    assert incentives_score([anon], "b2b", today=TODAY) == 50
