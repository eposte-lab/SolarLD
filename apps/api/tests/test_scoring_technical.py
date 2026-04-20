"""Unit tests for the Scoring Agent's technical subscore."""

from __future__ import annotations

from src.services.scoring import technical_score


def _roof(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "estimated_kwp": 10.0,
        "area_sqm": 100.0,
        "shading_score": 0.85,
        "exposure": "S",
        "pitch_degrees": 28.0,
        "has_existing_pv": False,
    }
    base.update(overrides)
    return base


def test_technical_ideal_south_25deg_kwp15() -> None:
    # 15 kWp / 85% shading / S / 28° → should be near the top of the scale
    score = technical_score(_roof(estimated_kwp=15.0))
    assert 80 <= score <= 100


def test_technical_big_industrial_saturates_at_100() -> None:
    # Requires perfect conditions — big array + S exposure + no shading.
    score = technical_score(
        _roof(
            estimated_kwp=60.0,
            area_sqm=400.0,
            pitch_degrees=30.0,
            shading_score=1.0,
            exposure="S",
        )
    )
    assert score == 100


def test_technical_existing_pv_zeros_score() -> None:
    score = technical_score(_roof(has_existing_pv=True, estimated_kwp=30.0))
    assert score == 0


def test_technical_north_exposure_strongly_penalized() -> None:
    south = technical_score(_roof(exposure="S"))
    north = technical_score(_roof(exposure="N"))
    assert north < south / 2  # N factor is 0.25, so at most 1/4 of S


def test_technical_full_shading_decimates() -> None:
    bright = technical_score(_roof(shading_score=1.0))
    shaded = technical_score(_roof(shading_score=0.1))
    assert shaded < bright * 0.3


def test_technical_tiny_roof_scores_low() -> None:
    score = technical_score(_roof(estimated_kwp=2.0, area_sqm=15.0))
    assert score < 40


def test_technical_extreme_pitch_penalized() -> None:
    flat = technical_score(_roof(pitch_degrees=2.0))
    sweet = technical_score(_roof(pitch_degrees=28.0))
    steep = technical_score(_roof(pitch_degrees=75.0))
    assert flat < sweet
    assert steep < sweet


def test_technical_missing_kwp_falls_back_to_area() -> None:
    # No kWp but 120 m² → ~20 kWp implied
    with_area = technical_score(_roof(estimated_kwp=None, area_sqm=120.0))
    without_area = technical_score(_roof(estimated_kwp=None, area_sqm=None))
    assert with_area > without_area == 0


def test_technical_missing_exposure_neutral() -> None:
    score = technical_score(_roof(exposure=None))
    # Neutral factor 0.75 should still land in a mid band for a decent roof.
    assert 30 <= score <= 80


def test_technical_unknown_shading_defaults_reasonable() -> None:
    score = technical_score(_roof(shading_score=None))
    # With 0.75 default shading, the score stays in a sensible band.
    assert 30 <= score <= 90


def test_technical_clamps_to_0_100() -> None:
    very_small = technical_score(_roof(estimated_kwp=0.1, area_sqm=0.5))
    very_big = technical_score(_roof(estimated_kwp=200.0))
    assert 0 <= very_small <= 100
    assert 0 <= very_big <= 100
