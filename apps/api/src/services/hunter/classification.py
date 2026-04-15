"""Best-effort B2B vs B2C classification from roof characteristics alone.

At the Hunter stage we have no identity data yet — the Identity agent will
later upgrade this with Visura / OpenCorporates. But we still need a
provisional `classification` on the roofs row so downstream agents can route
B2B vs B2C pipelines.

Heuristics:
  - Area > 300 m² → likely commercial/industrial (B2B)
  - Area > 120 m² AND kwp > 20 → likely B2B (large villa or small shop)
  - Otherwise → B2C
  - Shading >0.8, exposure = S/SW, area 40–120 → prime B2C residential

This is intentionally simple — it's replaced by the real classifier in
Sprint 2.
"""

from __future__ import annotations

from ...models.enums import SubjectType
from ..google_solar_service import RoofInsight

COMMERCIAL_AREA_THRESHOLD_SQM = 300.0
LARGE_MIXED_AREA_SQM = 120.0
LARGE_MIXED_KWP = 20.0


def classify_roof(insight: RoofInsight) -> SubjectType:
    """Return a preliminary SubjectType based on geometric features."""
    if insight.area_sqm >= COMMERCIAL_AREA_THRESHOLD_SQM:
        return SubjectType.B2B
    if insight.area_sqm >= LARGE_MIXED_AREA_SQM and insight.estimated_kwp >= LARGE_MIXED_KWP:
        return SubjectType.B2B
    if insight.area_sqm >= 30.0:
        return SubjectType.B2C
    return SubjectType.UNKNOWN


def is_likely_b2b_context(
    insight: RoofInsight,
    neighbour_count_within_100m: int,
) -> bool:
    """Heuristic enrichment: isolated + very large footprints are strong B2B.

    Dense clusters of medium-sized roofs are almost always residential
    condominiums.
    """
    if insight.area_sqm >= COMMERCIAL_AREA_THRESHOLD_SQM:
        return True
    if insight.area_sqm >= LARGE_MIXED_AREA_SQM and neighbour_count_within_100m <= 2:
        return True
    return False
