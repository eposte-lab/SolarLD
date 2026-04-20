"""Unit tests for the consumption subscore."""

from __future__ import annotations

from src.services.scoring import consumption_score


def _subject(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "type": "b2b",
        "ateco_code": "24.00",
        "employees": 30,
    }
    base.update(overrides)
    return base


def _roof(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "area_sqm": 200.0,
        "estimated_yearly_kwh": 20_000.0,
    }
    base.update(overrides)
    return base


def test_b2b_high_intensity_heavy_employee_count_scores_high() -> None:
    subject = _subject(ateco_code="24.00", employees=120)
    ateco = {
        "energy_intensity_tier": "high",
        "avg_yearly_kwh_per_sqm": 400.0,
    }
    score = consumption_score(subject, _roof(), ateco)
    assert 85 <= score <= 100


def test_b2b_low_intensity_small_company_scores_low() -> None:
    subject = _subject(ateco_code="52.00", employees=2)
    ateco = {"energy_intensity_tier": "low", "avg_yearly_kwh_per_sqm": 35.0}
    score = consumption_score(subject, _roof(), ateco)
    assert score < 50


def test_b2b_consumption_dwarfs_production_boosts_score() -> None:
    # Heavy consumption vs modest PV → ratio > 1.5 → +10
    roof = _roof(area_sqm=500.0, estimated_yearly_kwh=5_000.0)
    ateco = {"energy_intensity_tier": "high", "avg_yearly_kwh_per_sqm": 300.0}
    subject = _subject(employees=25)
    score = consumption_score(subject, roof, ateco)
    assert score >= 90


def test_b2b_production_dwarfs_consumption_penalizes_score() -> None:
    roof = _roof(area_sqm=50.0, estimated_yearly_kwh=50_000.0)
    ateco = {"energy_intensity_tier": "medium", "avg_yearly_kwh_per_sqm": 60.0}
    subject = _subject(employees=4)
    score = consumption_score(subject, roof, ateco)
    # baseline medium=50, no employee bonus, ratio<0.3 → -15 → ~35
    assert score <= 45


def test_b2b_missing_ateco_falls_back_on_employees() -> None:
    subject = _subject(ateco_code=None, employees=60)
    score = consumption_score(subject, _roof(), ateco_profile=None)
    assert score >= 70


def test_b2b_missing_everything_uses_area_proxy() -> None:
    subject = _subject(ateco_code=None, employees=None)
    roof = _roof(area_sqm=60.0)
    score = consumption_score(subject, roof, ateco_profile=None)
    assert 15 <= score <= 45  # B2C-style band for a ~60 m² footprint


def test_b2c_large_villa_scores_higher_than_small_apartment() -> None:
    big = consumption_score({"type": "b2c"}, _roof(area_sqm=280.0), None)
    small = consumption_score({"type": "b2c"}, _roof(area_sqm=45.0), None)
    assert big > small


def test_unknown_subject_uses_b2c_path() -> None:
    score = consumption_score({"type": "unknown"}, _roof(area_sqm=150.0), None)
    assert 40 <= score <= 80


def test_b2b_returns_int_in_0_100() -> None:
    subject = _subject(ateco_code="10.00", employees=500)
    ateco = {"energy_intensity_tier": "high", "avg_yearly_kwh_per_sqm": 220.0}
    score = consumption_score(subject, _roof(), ateco)
    assert isinstance(score, int)
    assert 0 <= score <= 100
