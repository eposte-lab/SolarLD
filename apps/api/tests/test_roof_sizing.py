"""Tests for realistic roof sizing — the trim that keeps the main installable
roof and drops Google's scattered slivers / steep faces, then recomputes the
headline kWp / kWh from the kept subset."""

from __future__ import annotations

import pytest

from src.core.config import settings
from src.services.google_solar_service import RoofInsight, SolarPanel
from src.services.roof_sizing import (
    apply_realistic_sizing,
    recompute_from_panels,
    select_realistic_panels,
)

# Segment layout for the fixtures:
#   seg 0 — big roof,   20 panels, pitch 15°  → KEEP
#   seg 1 — medium,     10 panels, pitch 20°  → KEEP (>= 8)
#   seg 2 — sliver,      3 panels, pitch 10°  → DROP (< 8 panels)
#   seg 3 — steep face, 12 panels, pitch 70°  → DROP (pitch > 50°)
_RAW = {
    "solarPotential": {
        "roofSegmentStats": [
            {"stats": {"areaMeters2": 100.0}, "pitchDegrees": 15.0},
            {"stats": {"areaMeters2": 50.0}, "pitchDegrees": 20.0},
            {"stats": {"areaMeters2": 10.0}, "pitchDegrees": 10.0},
            {"stats": {"areaMeters2": 40.0}, "pitchDegrees": 70.0},
        ]
    }
}
_PANEL_COUNTS = {0: 20, 1: 10, 2: 3, 3: 12}
_PANEL_KWH = 500.0
_PANEL_W = 400.0


def _panels() -> list[SolarPanel]:
    out: list[SolarPanel] = []
    for seg, n in _PANEL_COUNTS.items():
        for _ in range(n):
            out.append(
                SolarPanel(
                    lat=40.0,
                    lng=14.0,
                    orientation="LANDSCAPE",
                    segment_azimuth_deg=180.0,
                    yearly_energy_kwh=_PANEL_KWH,
                    segment_index=seg,
                )
            )
    return out


def _insight(panels: list[SolarPanel]) -> RoofInsight:
    total = len(panels)
    return RoofInsight(
        lat=40.0,
        lng=14.0,
        area_sqm=200.0,
        estimated_kwp=round(total * _PANEL_W / 1000.0, 2),
        estimated_yearly_kwh=round(total * _PANEL_KWH, 2),
        max_panel_count=total,
        panel_capacity_w=_PANEL_W,
        dominant_exposure="S",
        pitch_degrees=15.0,
        shading_score=1.0,
        postal_code=None,
        region_code=None,
        administrative_area=None,
        locality=None,
        raw=_RAW,
        panels=panels,
    )


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(settings, "realistic_sizing_enabled", True)
    monkeypatch.setattr(settings, "realistic_sizing_min_segment_fraction", 0.30)
    monkeypatch.setattr(settings, "realistic_sizing_max_pitch_deg", 50.0)
    return


def test_select_keeps_main_planes_drops_small_and_steep():
    # largest = seg 0 (20). threshold = 0.30 * 20 = 6.
    # seg 0 (20) ✓ · seg 1 (10) ✓ · seg 2 (3 < 6) ✗ · seg 3 (12 but pitch 70°) ✗
    kept = select_realistic_panels(_panels(), _RAW, min_segment_fraction=0.30, max_pitch_deg=50.0)
    assert {p.segment_index for p in kept} == {0, 1}
    assert len(kept) == 30


def test_higher_fraction_is_more_aggressive():
    # threshold = 0.60 * 20 = 12 → seg 1 (10 < 12) now also dropped.
    kept = select_realistic_panels(_panels(), _RAW, min_segment_fraction=0.60, max_pitch_deg=50.0)
    assert {p.segment_index for p in kept} == {0}
    assert len(kept) == 20


def test_apply_recomputes_kwp_and_kwh():
    trimmed = apply_realistic_sizing(_insight(_panels()))
    assert len(trimmed.panels) == 30
    assert trimmed.max_panel_count == 30
    assert trimmed.estimated_kwp == pytest.approx(30 * _PANEL_W / 1000.0)  # 12.0
    assert trimmed.estimated_yearly_kwh == pytest.approx(30 * _PANEL_KWH)  # 15000
    # Whole-roof area is preserved (drives consumption, not production).
    assert trimmed.area_sqm == 200.0
    # Raw payload is preserved intact (layout/delineation read panels from it).
    assert trimmed.raw is _RAW


def test_disabled_returns_unchanged():
    insight = _insight(_panels())
    with pytest.MonkeyPatch.context() as m:
        m.setattr(settings, "realistic_sizing_enabled", False)
        out = apply_realistic_sizing(insight)
    assert out is insight


def test_no_panels_returns_unchanged():
    insight = _insight([])
    assert apply_realistic_sizing(insight) is insight


def test_nothing_to_trim_returns_unchanged():
    # Only the big, shallow segment 0 → nothing gets dropped.
    panels = [p for p in _panels() if p.segment_index == 0]
    insight = _insight(panels)
    assert apply_realistic_sizing(insight) is insight


def test_fallback_keeps_largest_when_pitch_removes_all():
    # max_pitch 5° rejects every segment (pitches 15/20/10/70) → never zero out:
    # fall back to the segment with the most panels (seg 0, 20).
    kept = select_realistic_panels(_panels(), _RAW, min_segment_fraction=0.30, max_pitch_deg=5.0)
    assert {p.segment_index for p in kept} == {0}
    assert len(kept) == 20


def test_recompute_from_panels_math():
    insight = _insight(_panels())
    subset = [p for p in insight.panels if p.segment_index == 1]  # 10 panels
    out = recompute_from_panels(insight, subset)
    assert out.max_panel_count == 10
    assert out.estimated_kwp == pytest.approx(10 * _PANEL_W / 1000.0)  # 4.0
    assert out.estimated_yearly_kwh == pytest.approx(10 * _PANEL_KWH)  # 5000
