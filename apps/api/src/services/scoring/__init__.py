"""Scoring service — pure helpers consumed by the ScoringAgent.

Every function in this subpackage is **pure** (no DB, no HTTP): it takes
dataclasses / dicts and returns an integer 0–100 subscore, or a combined
final score via :func:`combine_breakdown`. This split exists so the full
algorithm can be exercised by unit tests without spinning up Supabase.
"""

from __future__ import annotations

from .combine import ScoringBreakdown, ScoringWeights, combine_breakdown, tier_for
from .consumption import consumption_score
from .distance import distance_score
from .geo import PROVINCE_TO_REGION, province_to_region
from .incentives import incentives_score
from .solvency import solvency_score
from .technical import technical_score

__all__ = [
    "PROVINCE_TO_REGION",
    "ScoringBreakdown",
    "ScoringWeights",
    "combine_breakdown",
    "consumption_score",
    "distance_score",
    "incentives_score",
    "province_to_region",
    "solvency_score",
    "technical_score",
    "tier_for",
]
