"""L3 — Building / Business quality filter (FLUSSO 1 v3).

Filters L2 candidates BEFORE the costly Solar API call. The PRD calls
this stage out as critical: "the building is the quality filter" — but
since real computer-vision-on-static-maps is overkill for the first
deploy, the MVP uses **simple heuristics** on Google Places signals:

    +1 if user_ratings_total >= 5     (real online presence)
    +2 if website is set              (strong professional signal)
    +1 if international_phone_number  (real business)
    +1 if business_status=OPERATIONAL (still trading)

A candidate passes when score >= 3. Computer-vision approach is staged
behind a feature flag (DISABLED) to be revisited in Sprint 5+.

Per-sector overrides for the minimum building area threshold (used in
the prompt context, not the filter gate itself for now) are kept here
for future iterations.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.logging import get_logger

log = get_logger(__name__)


# Future use — passed to the L5 prompt as context, also seed for the CV
# approach when we ship it. Min building area in m² considered "serious"
# for each sector.
MIN_BUILDING_AREA_M2_BY_SECTOR: dict[str, int] = {
    "industry_heavy": 800,
    "industry_light": 500,
    "food_production": 600,
    "logistics": 1000,
    "retail_gdo": 1500,
    "hospitality_large": 1000,
    "healthcare": 800,
    "agricultural_intensive": 500,
}

# MVP: heuristics threshold (out of 5)
HEURISTIC_PASS_THRESHOLD = 3


@dataclass(slots=True)
class QualityCheck:
    score: int
    passed: bool
    reasons: list[str]


def passes_filter_simple(
    *,
    user_ratings_total: int | None,
    website: str | None,
    phone: str | None,
    business_status: str | None,
) -> QualityCheck:
    """Score a candidate 0..5 using only Places metadata.

    Reasons list explains the score for downstream UI / debug.
    """
    score = 0
    reasons: list[str] = []

    if (user_ratings_total or 0) >= 5:
        score += 1
        reasons.append("ratings>=5")
    if website:
        score += 2
        reasons.append("website_present")
    if phone:
        score += 1
        reasons.append("phone_present")
    if (business_status or "").upper() == "OPERATIONAL":
        score += 1
        reasons.append("business_operational")

    return QualityCheck(
        score=score,
        passed=score >= HEURISTIC_PASS_THRESHOLD,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Future: computer-vision approach (Static Maps + polygon detection)
# ---------------------------------------------------------------------------
#
# When we revisit L3 with real building-area estimation (Sprint 5+):
#
#   def estimate_building_area_m2(lat, lng) -> float:
#       static_map = google_static_maps_fetch(lat, lng, zoom=19, size="640x640")
#       polygon = detect_main_building(static_map)  # opencv contour-find
#       return compute_polygon_area_m2(polygon)
#
# Cost: ~$0.001 per Static Maps call, free after 30-day cache via
# `known_company_buildings.solar_data_layers` (mig 0103).
#
# We deliberately ship the simple heuristic first because (a) the
# Solar API in L4 already validates roof area precisely, and (b) the
# heuristic correlates well with "serious business" — which is what
# we actually want to filter.
