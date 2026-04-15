"""Grid-sampling unit tests.

The grid generator is the foundation of the Hunter agent — a bug here
would either miss buildings or blow the Google Solar budget. Tests cover:

  - Basic bbox walking + deterministic order
  - Step-size respected (approximately, since lat/lng is curved)
  - `max_points` truncation
  - `start_index` resumption
  - Both bbox JSON shapes accepted
  - Invalid bbox raises
  - `estimate_grid_cost` returns the same count as the iterator
"""

from __future__ import annotations

import math

import pytest

from src.services.hunter.grid import (
    estimate_grid_cost,
    generate_sampling_grid,
    haversine_km,
)

# A ~200m × 200m bbox in central Naples (real coordinates, near Piazza Plebiscito)
NAPLES_BBOX = {
    "ne": {"lat": 40.8368, "lng": 14.2498},
    "sw": {"lat": 40.8350, "lng": 14.2478},
}


def test_grid_generates_points() -> None:
    points = list(generate_sampling_grid(NAPLES_BBOX, step_meters=50.0))
    assert len(points) > 0
    # Every point falls inside the bbox
    for p in points:
        assert 40.8350 - 1e-6 <= p.lat <= 40.8368 + 1e-6
        assert 14.2478 - 1e-6 <= p.lng <= 14.2498 + 1e-6


def test_grid_respects_step_spacing() -> None:
    points = list(generate_sampling_grid(NAPLES_BBOX, step_meters=50.0))
    # Adjacent row-0 points ≈ 50m apart
    row0 = [p for p in points if math.isclose(p.lat, points[0].lat, abs_tol=1e-6)]
    assert len(row0) >= 2
    d = haversine_km(row0[0].lat, row0[0].lng, row0[1].lat, row0[1].lng) * 1000
    assert 30.0 <= d <= 80.0, f"unexpected step distance {d:.1f}m"


def test_max_points_truncates() -> None:
    truncated = list(generate_sampling_grid(NAPLES_BBOX, step_meters=50.0, max_points=3))
    assert len(truncated) == 3


def test_start_index_resumes_mid_stream() -> None:
    full = list(generate_sampling_grid(NAPLES_BBOX, step_meters=50.0))
    assert len(full) >= 4
    resumed = list(generate_sampling_grid(NAPLES_BBOX, step_meters=50.0, start_index=2))
    assert resumed[0].index == full[2].index
    assert (resumed[0].lat, resumed[0].lng) == (full[2].lat, full[2].lng)


def test_accepts_north_south_east_west_shape() -> None:
    alt_bbox = {"north": 40.8368, "south": 40.8350, "east": 14.2498, "west": 14.2478}
    normal = list(generate_sampling_grid(NAPLES_BBOX, step_meters=50.0))
    alt = list(generate_sampling_grid(alt_bbox, step_meters=50.0))
    assert len(normal) == len(alt)


def test_invalid_bbox_raises() -> None:
    with pytest.raises(ValueError):
        list(
            generate_sampling_grid(
                {
                    "ne": {"lat": 40.0, "lng": 14.0},
                    "sw": {"lat": 41.0, "lng": 15.0},  # inverted
                }
            )
        )


def test_estimate_matches_iterator_count() -> None:
    est = estimate_grid_cost(NAPLES_BBOX, step_meters=50.0)
    actual = len(list(generate_sampling_grid(NAPLES_BBOX, step_meters=50.0)))
    # `estimate_grid_cost` uses an approximation → allow ±2 tolerance
    assert abs(est["grid_points"] - actual) <= 2
    assert est["estimated_cost_cents"] == est["grid_points"] * 2


def test_haversine_same_point_is_zero() -> None:
    assert haversine_km(40.0, 14.0, 40.0, 14.0) == 0.0


def test_haversine_known_distance() -> None:
    # Roma ↔ Milano ≈ 477 km
    d = haversine_km(41.9028, 12.4964, 45.4642, 9.1900)
    assert 470 < d < 485
