"""Unit tests for the Google Places discovery pure helpers (Sprint 9).

These never hit the network — all PlaceSummary instances are
hand-built fixtures.

Coverage:
  - `generate_search_cells` tiles the bbox with the expected count
    and every cell center lies inside (or on the boundary of) the bbox.
  - `dedupe_places` keeps first-seen ordering across overlapping batches.
  - `filter_operational` removes CLOSED_* statuses, keeps None/OPERATIONAL.
  - `rank_places` sorts by priority desc, then name asc (stable, deterministic).
  - `estimate_cost` scales linearly with cells and candidates.
"""

from __future__ import annotations

import pytest

from src.services.google_places_service import (
    DETAILS_COST_PER_CALL_CENTS,
    NEARBY_COST_PER_CALL_CENTS,
    PlaceSummary,
)
from src.services.hunter.places_discovery import (
    dedupe_places,
    estimate_cost,
    filter_operational,
    generate_search_cells,
    rank_places,
)

# ~15km × 15km bbox over Napoli metro — large enough to produce multiple
# cells at radius=5000.
NAPOLI_BBOX = {
    "ne": {"lat": 40.92, "lng": 14.35},
    "sw": {"lat": 40.78, "lng": 14.15},
}


def _place(
    pid: str,
    name: str,
    *,
    types: tuple[str, ...] = ("establishment",),
    status: str | None = "OPERATIONAL",
    lat: float = 40.85,
    lng: float = 14.25,
) -> PlaceSummary:
    return PlaceSummary(
        place_id=pid,
        name=name,
        address=None,
        lat=lat,
        lng=lng,
        business_status=status,
        types=types,
        primary_type=types[0] if types else None,
    )


# ---------------------------------------------------------------------------
# generate_search_cells
# ---------------------------------------------------------------------------


def test_search_cells_cover_bbox() -> None:
    cells = generate_search_cells(NAPOLI_BBOX, radius_m=5000.0)
    assert len(cells) >= 1, "expected at least one cell for a 15×15km bbox"
    # Every cell center lies within the bbox (or at the inset margin above).
    for c in cells:
        assert 40.78 <= c.center_lat <= 40.92 + 0.05, f"lat out of bbox: {c}"
        assert 14.15 <= c.center_lng <= 14.35 + 0.05, f"lng out of bbox: {c}"
        assert c.radius_m == 5000.0


def test_smaller_radius_generates_more_cells() -> None:
    big = generate_search_cells(NAPOLI_BBOX, radius_m=5000.0)
    small = generate_search_cells(NAPOLI_BBOX, radius_m=2000.0)
    assert len(small) > len(big), "smaller radius should produce more cells"


def test_search_cells_max_cap() -> None:
    cells = generate_search_cells(NAPOLI_BBOX, radius_m=500.0, max_cells=10)
    assert len(cells) == 10


def test_search_cells_unique_indices() -> None:
    cells = generate_search_cells(NAPOLI_BBOX, radius_m=3000.0)
    assert len(set(c.index for c in cells)) == len(cells)


# ---------------------------------------------------------------------------
# dedupe_places
# ---------------------------------------------------------------------------


def test_dedupe_preserves_first_seen_order() -> None:
    b1 = [_place("A", "Alpha"), _place("B", "Bravo")]
    b2 = [_place("B", "Bravo"), _place("C", "Charlie")]  # B duplicate
    b3 = [_place("D", "Delta"), _place("A", "Alpha")]  # A duplicate
    merged = dedupe_places([b1, b2, b3])
    assert [p.place_id for p in merged] == ["A", "B", "C", "D"]


def test_dedupe_empty_batches() -> None:
    assert dedupe_places([]) == []
    assert dedupe_places([[], []]) == []


# ---------------------------------------------------------------------------
# filter_operational
# ---------------------------------------------------------------------------


def test_filter_operational_keeps_open_and_missing_status() -> None:
    p_open = _place("A", "Open", status="OPERATIONAL")
    p_closed = _place("B", "Closed", status="CLOSED_PERMANENTLY")
    p_temp = _place("C", "Temp", status="CLOSED_TEMPORARILY")
    p_missing = _place("D", "Unknown", status=None)
    out = filter_operational([p_open, p_closed, p_temp, p_missing])
    assert [p.place_id for p in out] == ["A", "D"]


# ---------------------------------------------------------------------------
# rank_places
# ---------------------------------------------------------------------------


def test_rank_by_priority_desc_then_name_asc() -> None:
    priority = {"supermarket": 10, "hardware_store": 7, "bakery": 6}
    places = [
        _place("1", "Ferramenta Centro", types=("hardware_store",)),
        _place("2", "A Bakery", types=("bakery",)),
        _place("3", "Zebra Market", types=("supermarket",)),
        _place("4", "Alpha Super", types=("supermarket", "grocery_or_supermarket")),
    ]
    ranked = rank_places(places, priority)
    # Supermarkets first (priority 10), alpha sort within: "Alpha Super" < "Zebra Market"
    assert [p.place_id for p in ranked] == ["4", "3", "1", "2"]


def test_rank_stable_with_empty_priority() -> None:
    places = [_place("Z", "Zebra"), _place("A", "Alpha")]
    ranked = rank_places(places, {})
    # All tied at priority 0 → alphabetic by name
    assert [p.place_id for p in ranked] == ["A", "Z"]


def test_rank_uses_max_priority_across_types() -> None:
    priority = {"store": 3, "supermarket": 10}
    p = _place("1", "Co-op", types=("store", "supermarket"))
    q = _place("2", "Shop", types=("store",))
    ranked = rank_places([q, p], priority)
    assert [r.place_id for r in ranked] == ["1", "2"]  # p wins via supermarket=10


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------


def test_cost_estimate_scales_with_cells() -> None:
    est = estimate_cost(
        NAPOLI_BBOX,
        radius_m=5000.0,
        included_types=["supermarket", "warehouse"],
        expected_candidates=0,
    )
    assert est.cells > 0
    assert est.nearby_calls == est.cells * est.type_groups
    assert est.nearby_cost_cents == est.nearby_calls * NEARBY_COST_PER_CALL_CENTS
    assert est.details_calls_max == 0
    assert est.details_cost_cents == 0


def test_cost_estimate_includes_details() -> None:
    est = estimate_cost(
        NAPOLI_BBOX,
        radius_m=5000.0,
        included_types=["supermarket"],
        expected_candidates=50,
    )
    assert est.details_calls_max == 50
    assert est.details_cost_cents == 50 * DETAILS_COST_PER_CALL_CENTS
    assert est.total_cost_cents == est.nearby_cost_cents + est.details_cost_cents


def test_cost_estimate_empty_types_yields_zero_nearby() -> None:
    est = estimate_cost(
        NAPOLI_BBOX, radius_m=5000.0, included_types=[], expected_candidates=0
    )
    assert est.nearby_calls == 0
    assert est.nearby_cost_cents == 0
