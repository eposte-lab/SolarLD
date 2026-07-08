"""build_thermal_spec — the per-plane heat-map spec for the technical video.

Pure, no I/O: derives each roof plane's relative sun + normalised aerial
position from the stored Google Solar buildingInsights, with a graceful None
fallback when the data isn't there.
"""

from __future__ import annotations

from src.services.thermal_spec_service import build_thermal_spec

# Mirrors the real roofs.raw_data shape (buildingInsights).
_ROOF = {
    "raw_data": {
        "boundingBox": {
            "ne": {"latitude": 41.0706423, "longitude": 14.3061041},
            "sw": {"latitude": 41.0703599, "longitude": 14.3060274},
        },
        "solarPotential": {
            "maxSunshineHoursPerYear": 1680.0,
            "roofSegmentStats": [
                {
                    "stats": {
                        "areaMeters2": 178.7,
                        "sunshineQuantiles": [
                            318,
                            1464,
                            1507,
                            1515,
                            1521,
                            1525,
                            1529,
                            1533,
                            1538,
                            1545,
                            1680,
                        ],
                    },
                    "center": {"latitude": 41.0705056, "longitude": 14.3060651},
                },
                {
                    "stats": {
                        "areaMeters2": 90.0,
                        "sunshineQuantiles": [
                            200,
                            800,
                            820,
                            830,
                            840,
                            850,
                            860,
                            870,
                            880,
                            890,
                            900,
                        ],
                    },
                    "center": {"latitude": 41.0704, "longitude": 14.30605},
                },
            ],
        },
    },
    "derivations": {"estimated_kwp": 120, "realistic_yearly_savings_eur": 18000},
}


def test_thermal_spec_extracts_segments_and_heat() -> None:
    spec = build_thermal_spec(_ROOF)
    assert spec is not None
    assert len(spec.segments) == 2
    assert spec.max_sunshine_hours == 1680.0

    # Plane 0 gets more sun than plane 1 → hotter (higher intensity).
    assert spec.segments[0].intensity > spec.segments[1].intensity
    # Heat is roof-relative → the sunniest plane's median is ~0.9 of the max.
    assert 0.85 <= spec.segments[0].intensity <= 0.95
    # Every plane sits inside the aerial (normalised 0..1 coords).
    for seg in spec.segments:
        assert 0.0 <= seg.x <= 1.0
        assert 0.0 <= seg.y <= 1.0
        assert 0.0 <= seg.intensity <= 1.0

    # KPI passed straight through for the video's number cards.
    assert spec.derivations["estimated_kwp"] == 120


def test_thermal_spec_none_without_solar_data() -> None:
    assert build_thermal_spec({}) is None
    assert build_thermal_spec({"raw_data": {"solarPotential": {}}}) is None


def test_thermal_spec_none_without_bounding_box() -> None:
    roof = {"raw_data": {"solarPotential": _ROOF["raw_data"]["solarPotential"]}}
    assert build_thermal_spec(roof) is None
