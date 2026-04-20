"""Hunter support utilities: grid sampling, filters, classification,
and (Sprint 9) Google Places discovery helpers."""

from .classification import classify_roof, is_likely_b2b_context
from .filters import FilterVerdict, apply_technical_filters
from .grid import GridPoint, generate_sampling_grid, haversine_km
from .places_discovery import (
    DiscoveryCostEstimate,
    SearchCell,
    dedupe_places,
    estimate_cost,
    filter_operational,
    generate_search_cells,
    rank_places,
)

__all__ = [
    "DiscoveryCostEstimate",
    "FilterVerdict",
    "GridPoint",
    "SearchCell",
    "apply_technical_filters",
    "classify_roof",
    "dedupe_places",
    "estimate_cost",
    "filter_operational",
    "generate_sampling_grid",
    "generate_search_cells",
    "haversine_km",
    "is_likely_b2b_context",
    "rank_places",
]
