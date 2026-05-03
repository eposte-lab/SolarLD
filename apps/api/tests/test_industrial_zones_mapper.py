"""Tests for industrial_zones_mapper — pure logic + Overpass parser.

We don't hit the network: tests build OsmZone objects from canned
Overpass JSON fixtures and exercise the geometry helpers, query
builder, classifier and persistence flow.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from src.services import industrial_zones_mapper as izm
from src.services.sector_target_service import OsmTagHint, SectorAreaMapping


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def test_polygon_centroid_simple_square() -> None:
    # 1° square at the equator → centroid at (0.5, 0.5)
    coords = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]
    result = izm._polygon_centroid(coords)
    assert result is not None
    lat, lng = result
    assert lat == pytest.approx(0.5, abs=1e-6)
    assert lng == pytest.approx(0.5, abs=1e-6)


def test_polygon_centroid_open_ring_is_closed_automatically() -> None:
    # No explicit closure — centroid should still be (0.5, 0.5)
    coords = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]
    result = izm._polygon_centroid(coords)
    assert result is not None
    assert result[0] == pytest.approx(0.5, abs=1e-6)
    assert result[1] == pytest.approx(0.5, abs=1e-6)


def test_polygon_area_m2_unit_square_at_milan() -> None:
    # 0.001° × 0.001° square at Milan latitude (~45°N).
    # 0.001° lat ≈ 111.32 m, 0.001° lng ≈ 111.32 * cos(45°) ≈ 78.7 m.
    # Expected area: ~111.32 * 78.7 ≈ 8762 m² (small parking-lot scale).
    lat0 = 45.4642
    lng0 = 9.1900
    coords = [
        (lat0, lng0),
        (lat0, lng0 + 0.001),
        (lat0 + 0.001, lng0 + 0.001),
        (lat0 + 0.001, lng0),
    ]
    area = izm._polygon_area_m2(coords)
    assert 8000 < area < 9500  # Allow tolerance for equirectangular approximation.


def test_polygon_area_zero_for_degenerate() -> None:
    assert izm._polygon_area_m2([]) == 0.0
    assert izm._polygon_area_m2([(45.0, 9.0)]) == 0.0
    assert izm._polygon_area_m2([(45.0, 9.0), (45.001, 9.001)]) == 0.0


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------


def test_build_overpass_query_with_landuse_only() -> None:
    q = izm.build_overpass_query(
        landuse_values={"industrial", "commercial"},
        additional_tags=[],
        province_codes=["BS", "BG"],
    )
    assert "[out:json]" in q
    assert "IT-(BG|BS)" in q  # sorted unique
    assert "landuse" in q
    assert '"^(commercial|industrial)$"' in q
    assert "out tags geom" in q


def test_build_overpass_query_with_additional_tags() -> None:
    q = izm.build_overpass_query(
        landuse_values=set(),
        additional_tags=[
            OsmTagHint(tag_key="tourism", tag_value="hotel", weight=1.0),
            OsmTagHint(tag_key="building", tag_value="warehouse", weight=0.9),
        ],
        province_codes=["MI"],
    )
    assert '"tourism"="hotel"' in q
    assert '"building"="warehouse"' in q


def test_build_overpass_query_requires_provinces() -> None:
    with pytest.raises(ValueError):
        izm.build_overpass_query(
            landuse_values={"industrial"},
            additional_tags=[],
            province_codes=[],
        )


# ---------------------------------------------------------------------------
# Filter aggregation
# ---------------------------------------------------------------------------


def test_aggregate_filters_unions_landuse_and_dedupes_tags() -> None:
    cfg_a = SectorAreaMapping(
        wizard_group="industry_heavy",
        osm_landuse_hints=[OsmTagHint("landuse", "industrial", 1.0)],
        osm_additional_tags=[OsmTagHint("man_made", "works", 0.9)],
    )
    cfg_b = SectorAreaMapping(
        wizard_group="logistics",
        osm_landuse_hints=[
            OsmTagHint("landuse", "industrial", 1.0),
            OsmTagHint("landuse", "commercial", 0.7),
        ],
        osm_additional_tags=[
            OsmTagHint("building", "warehouse", 1.0),
            OsmTagHint("man_made", "works", 0.5),  # lower weight, should be dropped
        ],
    )
    landuse, tags = izm.aggregate_filters([cfg_a, cfg_b])

    assert landuse == {"industrial", "commercial"}
    # Two unique (key, value) pairs; man_made:works picked the higher weight.
    by_kv = {(t.tag_key, t.tag_value): t.weight for t in tags}
    assert by_kv == {("man_made", "works"): 0.9, ("building", "warehouse"): 1.0}


# ---------------------------------------------------------------------------
# Overpass parser
# ---------------------------------------------------------------------------


def _make_payload(elements: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": 0.6, "elements": elements}


def test_parse_overpass_extracts_polygon_and_centroid() -> None:
    # ~1.5 ha industrial polygon near Brescia.
    elem = {
        "type": "way",
        "id": 12345,
        "tags": {"landuse": "industrial", "name": "Zona PIP Castelmella"},
        "geometry": [
            {"lat": 45.5000, "lon": 10.1000},
            {"lat": 45.5005, "lon": 10.1000},
            {"lat": 45.5005, "lon": 10.1010},
            {"lat": 45.5000, "lon": 10.1010},
            {"lat": 45.5000, "lon": 10.1000},
        ],
    }
    zones = izm._parse_overpass_payload(_make_payload([elem]))
    assert len(zones) == 1
    z = zones[0]
    assert z.osm_id == 12345
    assert z.osm_type == "way"
    assert z.tags["landuse"] == "industrial"
    # Centroid roughly in the middle of the rectangle.
    assert 45.500 < z.centroid_lat < 45.501
    assert 10.100 < z.centroid_lng < 10.102
    assert z.area_m2 > 1000  # well above the 500 m² persistence floor
    assert z.geojson_polygon is not None
    assert z.geojson_polygon["type"] == "Polygon"


def test_parse_overpass_drops_tiny_polygons() -> None:
    # ~10 m² square — under the 500 m² persistence floor, must be dropped.
    elem = {
        "type": "way",
        "id": 9,
        "tags": {"landuse": "industrial"},
        "geometry": [
            {"lat": 45.0, "lon": 10.0},
            {"lat": 45.00003, "lon": 10.0},
            {"lat": 45.00003, "lon": 10.00003},
            {"lat": 45.0, "lon": 10.00003},
            {"lat": 45.0, "lon": 10.0},
        ],
    }
    zones = izm._parse_overpass_payload(_make_payload([elem]))
    assert zones == []


def test_parse_overpass_ignores_elements_without_geometry() -> None:
    elem = {"type": "way", "id": 1, "tags": {"landuse": "industrial"}}
    assert izm._parse_overpass_payload(_make_payload([elem])) == []


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def _make_zone(landuse: str | None = None, **extra_tags: str) -> izm.OsmZone:
    tags: dict[str, str] = {}
    if landuse:
        tags["landuse"] = landuse
    tags.update(extra_tags)
    return izm.OsmZone(
        osm_id=1,
        osm_type="way",
        centroid_lat=45.5,
        centroid_lng=10.0,
        area_m2=10_000,
        geojson_polygon=None,
        tags=tags,
    )


def test_classify_zone_matches_landuse_industrial() -> None:
    industry = SectorAreaMapping(
        wizard_group="industry_heavy",
        osm_landuse_hints=[OsmTagHint("landuse", "industrial", 1.0)],
        min_zone_area_m2=5000,
    )
    zone = _make_zone(landuse="industrial")
    classified = izm.classify_zone_for_sectors(zone, configs=[industry])
    assert classified.matched_sectors == ["industry_heavy"]
    assert classified.primary_sector == "industry_heavy"
    assert classified.matching_score == pytest.approx(100.0, abs=1e-3)


def test_classify_zone_matches_multiple_sectors_and_picks_highest() -> None:
    industry = SectorAreaMapping(
        wizard_group="industry_heavy",
        osm_landuse_hints=[OsmTagHint("landuse", "industrial", 1.0)],
        min_zone_area_m2=5000,
    )
    logistics = SectorAreaMapping(
        wizard_group="logistics",
        osm_landuse_hints=[
            OsmTagHint("landuse", "industrial", 0.8),
            OsmTagHint("landuse", "commercial", 1.0),
        ],
        osm_additional_tags=[OsmTagHint("building", "warehouse", 1.0)],
        min_zone_area_m2=5000,
    )
    zone = _make_zone(landuse="industrial", building="warehouse")
    classified = izm.classify_zone_for_sectors(
        zone, configs=[industry, logistics]
    )
    # Both match; logistics wins because warehouse weight 1.0 ties with
    # industrial weight 1.0 for industry_heavy. Sort is stable; either
    # ordering acceptable but both must be in matched_sectors.
    assert set(classified.matched_sectors) == {"industry_heavy", "logistics"}
    assert classified.primary_sector in {"industry_heavy", "logistics"}
    assert classified.matching_score == pytest.approx(100.0, abs=1e-3)


def test_classify_zone_below_min_area_halves_score() -> None:
    industry = SectorAreaMapping(
        wizard_group="industry_heavy",
        osm_landuse_hints=[OsmTagHint("landuse", "industrial", 1.0)],
        min_zone_area_m2=20_000,  # zone is 10k → below threshold
    )
    zone = _make_zone(landuse="industrial")
    classified = izm.classify_zone_for_sectors(zone, configs=[industry])
    # Score halved 1.0 → 0.5, still above 0.30 threshold so still matched.
    assert classified.matched_sectors == ["industry_heavy"]
    assert classified.matching_score == pytest.approx(50.0, abs=1e-3)


def test_classify_zone_below_threshold_drops_match() -> None:
    industry = SectorAreaMapping(
        wizard_group="industry_heavy",
        # Weight 0.5 with min_area gate (1.0 → 0.25) is below 0.30 threshold.
        osm_landuse_hints=[OsmTagHint("landuse", "industrial", 0.5)],
        min_zone_area_m2=20_000,
    )
    zone = _make_zone(landuse="industrial")
    classified = izm.classify_zone_for_sectors(zone, configs=[industry])
    assert classified.matched_sectors == []
    assert classified.primary_sector is None


def test_classify_zone_with_no_landuse_only_matches_via_additional_tags() -> None:
    hospitality = SectorAreaMapping(
        wizard_group="hospitality_large",
        osm_additional_tags=[OsmTagHint("tourism", "hotel", 1.0)],
        min_zone_area_m2=1000,
    )
    zone = _make_zone(landuse=None, tourism="hotel")
    classified = izm.classify_zone_for_sectors(zone, configs=[hospitality])
    assert classified.primary_sector == "hospitality_large"
    assert classified.matching_score == pytest.approx(100.0, abs=1e-3)
