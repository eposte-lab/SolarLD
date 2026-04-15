"""Filter + classification unit tests.

These have no network/DB dependencies — they test pure functions against
hand-crafted `RoofInsight` values.
"""

from __future__ import annotations

from src.models.enums import SubjectType
from src.services.google_solar_service import RoofInsight
from src.services.hunter.classification import classify_roof
from src.services.hunter.filters import apply_technical_filters


def _insight(**overrides: object) -> RoofInsight:
    base = {
        "lat": 40.83,
        "lng": 14.25,
        "area_sqm": 100.0,
        "estimated_kwp": 10.0,
        "estimated_yearly_kwh": 13_000.0,
        "max_panel_count": 25,
        "panel_capacity_w": 400.0,
        "dominant_exposure": "S",
        "pitch_degrees": 25.0,
        "shading_score": 0.8,
        "postal_code": "80100",
        "region_code": "IT-NA",
        "administrative_area": "Napoli",
        "locality": "Napoli",
        "raw": {},
    }
    base.update(overrides)
    return RoofInsight(**base)  # type: ignore[arg-type]


# ----- Filters -----


def test_filter_accepts_good_roof() -> None:
    assert apply_technical_filters(_insight()).accepted is True


def test_filter_rejects_tiny_area() -> None:
    v = apply_technical_filters(_insight(area_sqm=10.0))
    assert v.accepted is False
    assert v.reason is not None and "area" in v.reason


def test_filter_rejects_low_kwp() -> None:
    v = apply_technical_filters(_insight(estimated_kwp=1.0))
    assert v.accepted is False
    assert v.reason is not None and "kwp" in v.reason


def test_filter_rejects_heavy_shading() -> None:
    v = apply_technical_filters(_insight(shading_score=0.1))
    assert v.accepted is False
    assert v.reason is not None and "shading" in v.reason


def test_filter_rejects_north_exposure() -> None:
    v = apply_technical_filters(_insight(dominant_exposure="N"))
    assert v.accepted is False
    assert v.reason is not None and "exposure" in v.reason


def test_filter_rejects_extreme_pitch() -> None:
    v = apply_technical_filters(_insight(pitch_degrees=75.0))
    assert v.accepted is False
    assert v.reason is not None and "pitch" in v.reason


def test_filter_accepts_flat_roof() -> None:
    # 5° is the floor, should pass
    assert apply_technical_filters(_insight(pitch_degrees=5.0)).accepted is True


# ----- Classification -----


def test_classify_large_commercial_is_b2b() -> None:
    assert classify_roof(_insight(area_sqm=500.0)) == SubjectType.B2B


def test_classify_medium_high_power_is_b2b() -> None:
    assert classify_roof(_insight(area_sqm=150.0, estimated_kwp=25.0)) == SubjectType.B2B


def test_classify_small_residential_is_b2c() -> None:
    assert classify_roof(_insight(area_sqm=80.0, estimated_kwp=8.0)) == SubjectType.B2C


def test_classify_micro_is_unknown() -> None:
    assert classify_roof(_insight(area_sqm=15.0)) == SubjectType.UNKNOWN
