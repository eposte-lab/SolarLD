"""Unit tests for `HunterAgent._apply_config_filters` (Sprint 9).

The dispatcher swaps the default `apply_technical_filters` for a
per-tenant variant driven by `TechnicalFilters` from `tenant_configs`.
This test verifies accept/reject logic for every filter dimension,
without touching the DB or the network.

Key semantics under test:
  - area_sqm < min_area_sqm           → reject
  - estimated_kwp < min_kwp           → reject
  - shading_score < 1 - max_shading   → reject
    (Google: shading_score 1.0 = unobstructed; config max_shading
     = how much *obstruction* we tolerate)
  - dominant_exposure == "N"          → reject
  - otherwise                         → accept
"""

from __future__ import annotations

from src.agents.hunter import _apply_config_filters
from src.services.google_solar_service import RoofInsight
from src.services.tenant_config_service import TechnicalFilters


def _insight(
    *,
    area: float = 600.0,
    kwp: float = 60.0,
    shading: float = 0.9,
    exposure: str = "S",
) -> RoofInsight:
    return RoofInsight(
        lat=40.85,
        lng=14.25,
        area_sqm=area,
        estimated_kwp=kwp,
        estimated_yearly_kwh=kwp * 1200,
        max_panel_count=int(kwp * 3),
        panel_capacity_w=400.0,
        dominant_exposure=exposure,
        pitch_degrees=15.0,
        shading_score=shading,
        postal_code="80100",
        region_code="IT-72",
        administrative_area="Campania",
        locality="Napoli",
        raw={},
    )


# Filter tuned for B2B scan: large roofs, strong yield, mostly open sky.
B2B = TechnicalFilters(
    min_area_sqm=500.0,
    min_kwp=50.0,
    max_shading=0.4,  # reject when shading_score < 0.6
    min_exposure_score=0.7,
)


def test_accepts_when_above_all_thresholds() -> None:
    v = _apply_config_filters(_insight(), B2B)
    assert v.accepted is True
    assert v.reason is None


def test_rejects_small_area() -> None:
    v = _apply_config_filters(_insight(area=200.0), B2B)
    assert v.accepted is False
    assert "area" in (v.reason or "")


def test_rejects_low_kwp() -> None:
    v = _apply_config_filters(_insight(kwp=10.0), B2B)
    assert v.accepted is False
    assert "kwp" in (v.reason or "")


def test_rejects_heavily_shaded_roof() -> None:
    # shading_score 0.5 < 1 - 0.4 = 0.6 → reject
    v = _apply_config_filters(_insight(shading=0.5), B2B)
    assert v.accepted is False
    assert "shading" in (v.reason or "")


def test_accepts_when_shading_at_threshold() -> None:
    # shading_score 0.6 == 1 - 0.4 → not strictly less → accept
    v = _apply_config_filters(_insight(shading=0.6), B2B)
    assert v.accepted is True


def test_rejects_north_exposure() -> None:
    v = _apply_config_filters(_insight(exposure="N"), B2B)
    assert v.accepted is False
    assert "exposure" in (v.reason or "")


def test_accepts_non_north_exposures() -> None:
    for exp in ("NE", "E", "SE", "S", "SW", "W", "NW"):
        v = _apply_config_filters(_insight(exposure=exp), B2B)
        assert v.accepted is True, f"expected to accept exposure={exp}"


def test_permissive_filter_accepts_everything() -> None:
    # All thresholds at floor — should accept almost any roof.
    loose = TechnicalFilters(
        min_area_sqm=0, min_kwp=0, max_shading=1.0, min_exposure_score=0.0
    )
    v = _apply_config_filters(_insight(area=10, kwp=1, shading=0.0, exposure="E"), loose)
    assert v.accepted is True


def test_b2c_thresholds_accept_small_residential_roof() -> None:
    # A residential-scale filter. A 60m² 3kWp south-facing roof should pass.
    b2c = TechnicalFilters(
        min_area_sqm=60.0,
        min_kwp=3.0,
        max_shading=0.5,
        min_exposure_score=0.6,
    )
    v = _apply_config_filters(_insight(area=60, kwp=3.5, shading=0.55, exposure="S"), b2c)
    assert v.accepted is True
