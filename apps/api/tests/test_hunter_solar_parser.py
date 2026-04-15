"""Parser tests for the Google Solar `buildingInsights:findClosest` payload.

Uses a hand-crafted fixture that mirrors the real API shape so we don't
depend on a live API key during CI.
"""

from __future__ import annotations

from src.services.google_solar_service import (
    _azimuth_to_cardinal,
    _parse_building_insight_payload,
)

SAMPLE_PAYLOAD = {
    "name": "buildings/ChIJL_solar",
    "center": {"latitude": 40.8361, "longitude": 14.2487},
    "postalCode": "80132",
    "administrativeArea": "Napoli",
    "regionCode": "IT",
    "locality": "Napoli",
    "solarPotential": {
        "maxArrayPanelsCount": 30,
        "panelCapacityWatts": 400.0,
        "wholeRoofStats": {"areaMeters2": 180.5},
        "roofSegmentStats": [
            {
                "azimuthDegrees": 175.0,  # ~South
                "pitchDegrees": 22.5,
                "stats": {
                    "areaMeters2": 120.0,
                    "sunshineQuantiles": [800, 1000, 1200, 1400, 1500],
                },
            },
            {
                "azimuthDegrees": 355.0,  # ~North, smaller segment
                "pitchDegrees": 22.5,
                "stats": {
                    "areaMeters2": 60.5,
                    "sunshineQuantiles": [200, 300, 400, 500, 600],
                },
            },
        ],
        "solarPanels": [
            {"yearlyEnergyDcKwh": 500.0},
            {"yearlyEnergyDcKwh": 500.0},
            {"yearlyEnergyDcKwh": 500.0},
        ],
    },
}


def test_parser_extracts_dominant_south_exposure() -> None:
    insight = _parse_building_insight_payload(SAMPLE_PAYLOAD)
    assert insight.dominant_exposure == "S"
    assert insight.pitch_degrees == 22.5


def test_parser_computes_kwp_from_panel_count() -> None:
    insight = _parse_building_insight_payload(SAMPLE_PAYLOAD)
    # 30 panels × 400 W / 1000 = 12 kWp
    assert insight.estimated_kwp == 12.0


def test_parser_prefers_panel_sum_for_yearly_kwh() -> None:
    insight = _parse_building_insight_payload(SAMPLE_PAYLOAD)
    # 3 × 500 = 1500 kWh (just the sum of the explicit panels)
    assert insight.estimated_yearly_kwh == 1500.0


def test_parser_reads_whole_roof_area() -> None:
    insight = _parse_building_insight_payload(SAMPLE_PAYLOAD)
    assert insight.area_sqm == 180.5


def test_parser_falls_back_when_no_segments() -> None:
    payload = {
        "center": {"latitude": 1.0, "longitude": 2.0},
        "solarPotential": {
            "maxArrayPanelsCount": 10,
            "panelCapacityWatts": 400.0,
            "wholeRoofStats": {"areaMeters2": 50.0},
            "roofSegmentStats": [],
        },
    }
    insight = _parse_building_insight_payload(payload)
    # Defaults: S @ 20° pitch, kWh = kwp × 1300
    assert insight.dominant_exposure == "S"
    assert insight.estimated_kwp == 4.0
    assert insight.estimated_yearly_kwh == 5200.0


def test_azimuth_cardinal_buckets() -> None:
    # Exact compass points
    assert _azimuth_to_cardinal(0) == "N"
    assert _azimuth_to_cardinal(45) == "NE"
    assert _azimuth_to_cardinal(90) == "E"
    assert _azimuth_to_cardinal(135) == "SE"
    assert _azimuth_to_cardinal(180) == "S"
    assert _azimuth_to_cardinal(225) == "SW"
    assert _azimuth_to_cardinal(270) == "W"
    assert _azimuth_to_cardinal(315) == "NW"
    # Sector boundaries
    assert _azimuth_to_cardinal(22) == "N"
    assert _azimuth_to_cardinal(23) == "NE"
    # Wrap-around
    assert _azimuth_to_cardinal(359) == "N"
    assert _azimuth_to_cardinal(360) == "N"
