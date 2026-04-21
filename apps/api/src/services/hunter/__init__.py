"""Hunter support utilities: grid sampling, filters, classification.

Post-v2 cleanup: Google Places discovery helpers (places_discovery.py)
were deleted along with the `b2b_precision` mode in April 2026. The
grid + filter helpers stay because the B2B funnel L4 stage and a
handful of scoring utilities still consume them (`apply_technical_filters`
via `FilterVerdict`, `classify_roof`, `haversine_km`).
"""

from .classification import classify_roof, is_likely_b2b_context
from .filters import FilterVerdict, apply_technical_filters
from .grid import GridPoint, generate_sampling_grid, haversine_km

__all__ = [
    "FilterVerdict",
    "GridPoint",
    "apply_technical_filters",
    "classify_roof",
    "generate_sampling_grid",
    "haversine_km",
    "is_likely_b2b_context",
]
