"""Pure-function helpers for the Google Places discovery loop.

Split out of `HunterAgent._run_b2b_precision` so the logic can be
unit-tested without the network.

Coverage pattern:
  1. `generate_search_cells(bbox, radius_m)` — tessellate the bbox
     with overlapping circles each used for one Nearby Search call.
  2. `dedupe_places(cells)` — merge results across cells keeping the
     first occurrence of each place_id.
  3. `rank_places(places, priority_map)` — sort by Google-type priority
     (higher first) then by name for determinism.
  4. `estimate_cost(bbox, radius_m, type_count)` — pre-scan budget check.

All functions are synchronous and side-effect-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..google_places_service import (
    DETAILS_COST_PER_CALL_CENTS,
    NEARBY_COST_PER_CALL_CENTS,
    PlaceSummary,
)
from .grid import METERS_PER_DEG_LAT, _meters_per_deg_lng, _normalize_bbox


@dataclass(frozen=True, slots=True)
class SearchCell:
    """A single Nearby Search call: a circle over the territory."""

    index: int
    center_lat: float
    center_lng: float
    radius_m: float


def generate_search_cells(
    bbox: dict[str, Any],
    *,
    radius_m: float = 5000.0,
    max_cells: int | None = None,
) -> list[SearchCell]:
    """Tile the bbox with circles of `radius_m` spaced at √2·radius
    (so neighbor circles just cover each other's corners).

    Typical Italian territories (e.g. Campania capoluogo area ≈ 50km²):
      radius_m=5000 → ~3 cells
      radius_m=3000 → ~6 cells

    Smaller radii cost more Nearby calls but give denser coverage on
    dense types (supermarkets cluster). Hunter defaults to 5000m and
    lets budget caps do the rest.
    """
    south, west, north, east = _normalize_bbox(bbox)

    # Step between cell centers so that adjacent circles overlap the
    # diagonal midpoint (covers corners): step = radius * √2.
    step_m = radius_m * math.sqrt(2)

    # Convert step to degrees — use the mid-lat so lng step doesn't
    # drift across the bbox (good enough for territory scale).
    mid_lat = (south + north) / 2.0
    lat_step_deg = step_m / METERS_PER_DEG_LAT
    lng_step_deg = step_m / _meters_per_deg_lng(mid_lat)

    # Inset so the first cell center is radius-away from the edge; this
    # means the outer circle still covers the corner.
    start_lat = south + (lat_step_deg / 2.0)
    start_lng = west + (lng_step_deg / 2.0)

    cells: list[SearchCell] = []
    idx = 0
    lat = start_lat
    while lat <= north + 1e-9:
        lng = start_lng
        while lng <= east + 1e-9:
            cells.append(SearchCell(index=idx, center_lat=lat, center_lng=lng, radius_m=radius_m))
            idx += 1
            if max_cells is not None and len(cells) >= max_cells:
                return cells
            lng += lng_step_deg
        lat += lat_step_deg
    return cells


def dedupe_places(batches: list[list[PlaceSummary]]) -> list[PlaceSummary]:
    """Flatten and dedupe by `place_id`, keeping the first occurrence.

    Google Places returns the same place_id from multiple overlapping
    circles — this merge preserves order (first-seen wins) so results
    are stable across runs given the same cell iteration order.
    """
    seen: set[str] = set()
    out: list[PlaceSummary] = []
    for batch in batches:
        for p in batch:
            if p.place_id in seen:
                continue
            seen.add(p.place_id)
            out.append(p)
    return out


def rank_places(
    places: list[PlaceSummary],
    priority_map: dict[str, int] | None = None,
) -> list[PlaceSummary]:
    """Stable sort by priority (desc), then name (asc).

    Priority is computed as the max `priority_map[type]` across a
    place's `types` — so a supermarket tagged
    `['supermarket','grocery_or_supermarket']` wins even if only one
    of the two types is in the map.
    """
    pm = priority_map or {}

    def score(p: PlaceSummary) -> int:
        if not pm:
            return 0
        return max((pm.get(t, 0) for t in p.types), default=0)

    return sorted(places, key=lambda p: (-score(p), p.name.lower(), p.place_id))


def filter_operational(places: list[PlaceSummary]) -> list[PlaceSummary]:
    """Drop `CLOSED_PERMANENTLY` / `CLOSED_TEMPORARILY`. Preserves order.

    `business_status=None` is allowed — Google omits the field for
    long-established places.
    """
    return [p for p in places if p.is_operational]


@dataclass(frozen=True, slots=True)
class DiscoveryCostEstimate:
    """Rough pre-run budget breakdown for b2b_precision mode."""

    cells: int
    type_groups: int
    nearby_calls: int
    nearby_cost_cents: int
    # Details are optional per-lead; cap at `expected_candidates`.
    details_calls_max: int
    details_cost_cents: int
    total_cost_cents: int


def estimate_cost(
    bbox: dict[str, Any],
    *,
    radius_m: float = 5000.0,
    included_types: list[str],
    expected_candidates: int = 0,
) -> DiscoveryCostEstimate:
    """Estimate pre-scan costs for Google Places.

    Rough model:
      nearby_calls = cells × type_groups
      (one Nearby call per type set per cell — Google accepts a list
      of types per call, so `type_groups=1` unless the caller chunks)

      details_calls = expected_candidates (upper bound: one call per
      candidate that passes the Solar filter)
    """
    cells = generate_search_cells(bbox, radius_m=radius_m)
    type_groups = 1 if included_types else 0
    nearby_calls = len(cells) * type_groups
    nearby_cents = nearby_calls * NEARBY_COST_PER_CALL_CENTS
    details_cents = expected_candidates * DETAILS_COST_PER_CALL_CENTS

    return DiscoveryCostEstimate(
        cells=len(cells),
        type_groups=type_groups,
        nearby_calls=nearby_calls,
        nearby_cost_cents=nearby_cents,
        details_calls_max=expected_candidates,
        details_cost_cents=details_cents,
        total_cost_cents=nearby_cents + details_cents,
    )
