"""Claude Vision response parser tests.

These cover `parse_vision_response` + `projection_to_insight` — the two
pure functions that convert Claude's JSON output into a RoofInsight.
Everything else in the module talks to the Anthropic API.
"""

from __future__ import annotations

import json

from src.services.claude_vision_service import (
    parse_vision_response,
    projection_to_insight,
)


def _payload(**overrides: object) -> str:
    base = {
        "has_building": True,
        "confidence": 0.9,
        "area_sqm": 120.0,
        "azimuth_degrees": 180.0,
        "pitch_degrees": 25.0,
        "shading_score": 0.8,
        "has_existing_pv": False,
        "notes": "clear residential roof",
    }
    base.update(overrides)
    return json.dumps(base)


def test_parser_accepts_clean_json() -> None:
    data = parse_vision_response(_payload())
    assert data is not None
    assert data["has_building"] is True
    assert data["area_sqm"] == 120.0


def test_parser_strips_code_fences() -> None:
    fenced = f"```json\n{_payload()}\n```"
    data = parse_vision_response(fenced)
    assert data is not None
    assert data["area_sqm"] == 120.0


def test_parser_rejects_has_building_false() -> None:
    assert parse_vision_response(_payload(has_building=False)) is None


def test_parser_rejects_low_confidence() -> None:
    assert parse_vision_response(_payload(confidence=0.7)) is None


def test_parser_rejects_malformed_json() -> None:
    assert parse_vision_response("not valid json") is None
    assert parse_vision_response('{"partial": true') is None


def test_parser_rejects_missing_fields() -> None:
    partial = json.dumps(
        {"has_building": True, "confidence": 0.9, "area_sqm": 100.0}
    )
    assert parse_vision_response(partial) is None


def test_parser_clamps_out_of_range_values() -> None:
    data = parse_vision_response(
        _payload(pitch_degrees=999.0, shading_score=5.0, azimuth_degrees=720.5)
    )
    assert data is not None
    assert data["pitch_degrees"] == 90.0
    assert data["shading_score"] == 1.0
    assert 0 <= data["azimuth_degrees"] < 360


def test_projection_computes_conservative_kwp() -> None:
    raw = parse_vision_response(_payload(area_sqm=120.0, shading_score=0.8))
    assert raw is not None
    insight = projection_to_insight(raw, lat=40.83, lng=14.25)
    # 120 * 0.85 / 6 = 17 kWp
    assert 16.0 <= insight.estimated_kwp <= 18.0
    # kWh = kwp * 1300 * shading = ~17 * 1300 * 0.8
    assert 17_000 <= insight.estimated_yearly_kwh <= 18_500


def test_projection_sets_vision_source_marker() -> None:
    raw = parse_vision_response(_payload())
    assert raw is not None
    insight = projection_to_insight(raw, lat=40.83, lng=14.25)
    assert insight.raw.get("source") == "claude_vision"


def test_projection_dominant_exposure_from_azimuth() -> None:
    raw = parse_vision_response(_payload(azimuth_degrees=225.0))
    assert raw is not None
    insight = projection_to_insight(raw, lat=40.83, lng=14.25)
    assert insight.dominant_exposure == "SW"
