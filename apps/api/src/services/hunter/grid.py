"""Grid sampling inside a territory bounding box.

The Google Solar API is point-query only. To enumerate every building in a
territory we cannot just call a bbox endpoint — instead we lay a regular grid
of sample points and query each one. 50m spacing hits every urban building
without too much redundancy (Italian urban footprint median ≈ 120m²).

Sampling strategy:
  1) Convert the tenant-selected bbox to an equal-angle lat/lng grid.
  2) Step size computed from a target `step_meters` using the classic
     earth-radius approximation (lat step = m / 111_320, lng step = m /
     (111_320 * cos(lat))).
  3) For each point the Hunter agent:
       a) hashes (lat,lng) → geohash(8) = ~19m cell → dedupes against the
          `roofs` table.
       b) calls Google Solar.
       c) if 404 → runs the Mapbox vision fallback.

To stay within per-scan budgets the caller can pass `max_points` which
truncates the iterator — subsequent scans continue via the
`next_pagination_token` (row index) pattern.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

EARTH_RADIUS_M = 6_371_000.0
METERS_PER_DEG_LAT = 111_320.0


@dataclass(frozen=True, slots=True)
class GridPoint:
    """A single sampling coordinate + its index in the grid."""

    index: int
    lat: float
    lng: float


def _meters_per_deg_lng(lat_deg: float) -> float:
    """How many meters is one degree of longitude at this latitude?"""
    return METERS_PER_DEG_LAT * math.cos(math.radians(lat_deg))


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two points."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return (EARTH_RADIUS_M * c) / 1000.0


def _normalize_bbox(bbox: dict[str, Any]) -> tuple[float, float, float, float]:
    """Normalize a tenant-provided bbox JSON to (south, west, north, east).

    Accepted shapes:
      - {"ne": {"lat": ..., "lng": ...}, "sw": {...}}
      - {"north": ..., "south": ..., "east": ..., "west": ...}
    """
    if "ne" in bbox and "sw" in bbox:
        north = float(bbox["ne"]["lat"])
        east = float(bbox["ne"]["lng"])
        south = float(bbox["sw"]["lat"])
        west = float(bbox["sw"]["lng"])
    else:
        north = float(bbox["north"])
        south = float(bbox["south"])
        east = float(bbox["east"])
        west = float(bbox["west"])

    if north < south or east < west:
        raise ValueError(f"invalid bbox — ne/sw out of order: {bbox}")
    # Italy-sanity check (rough): 35°N–48°N, 6°E–19°E
    if not (30.0 <= south <= 50.0 and 0.0 <= west <= 25.0):
        # Still allow (e.g. tests) but log-worthy. Don't raise.
        pass
    return south, west, north, east


def generate_sampling_grid(
    bbox: dict[str, Any],
    *,
    step_meters: float = 50.0,
    max_points: int | None = None,
    start_index: int = 0,
) -> Iterator[GridPoint]:
    """Yield `GridPoint`s covering the bbox.

    The generator starts at `start_index` so repeat scans can resume from a
    previous pagination token without re-computing the prefix.
    """
    south, west, north, east = _normalize_bbox(bbox)

    # lat step is constant; lng step varies with latitude → recompute per row
    lat_step_deg = step_meters / METERS_PER_DEG_LAT
    if lat_step_deg <= 0:
        raise ValueError("step_meters must be positive")

    n_lat = max(1, int(math.ceil((north - south) / lat_step_deg)))

    idx = 0
    yielded = 0
    for i in range(n_lat + 1):
        lat = south + i * lat_step_deg
        if lat > north:
            lat = north
        mpd_lng = _meters_per_deg_lng(lat)
        if mpd_lng <= 0:  # polar degenerate
            continue
        lng_step_deg = step_meters / mpd_lng
        n_lng = max(1, int(math.ceil((east - west) / lng_step_deg)))
        for j in range(n_lng + 1):
            lng = west + j * lng_step_deg
            if lng > east:
                lng = east
            if idx >= start_index:
                yield GridPoint(index=idx, lat=lat, lng=lng)
                yielded += 1
                if max_points is not None and yielded >= max_points:
                    return
            idx += 1


def estimate_grid_cost(
    bbox: dict[str, Any],
    *,
    step_meters: float = 50.0,
    cost_per_call_cents: int = 2,
) -> dict[str, int]:
    """Rough pre-scan budget check: how many points + cents would we spend."""
    south, west, north, east = _normalize_bbox(bbox)
    mid_lat = (south + north) / 2.0
    lat_step_deg = step_meters / METERS_PER_DEG_LAT
    lng_step_deg = step_meters / _meters_per_deg_lng(mid_lat)
    n_lat = max(1, int(math.ceil((north - south) / lat_step_deg))) + 1
    n_lng = max(1, int(math.ceil((east - west) / lng_step_deg))) + 1
    total = n_lat * n_lng
    return {
        "grid_points": total,
        "estimated_cost_cents": total * cost_per_call_cents,
    }
