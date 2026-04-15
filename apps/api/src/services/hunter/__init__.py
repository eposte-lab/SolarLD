"""Hunter support utilities: grid sampling, filters, classification."""

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
