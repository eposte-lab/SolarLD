"""Breakdown + weighted combiner + tier mapping.

The V1 default weights from the PRD are seeded into `scoring_weights` at
migration time and look like::

    {"technical": 25, "consumption": 25, "incentives": 15,
     "solvency": 20, "distance": 15}

All five dimensions are expected to sum to 100, but this module
normalizes anyway so a mis-seeded row never produces scores > 100.

Tier thresholds match the PRD:
    > 75          → HOT
    60..75        → WARM
    40..59        → COLD
    < 40          → REJECTED

Sprint 9: `tier_for` also honours a per-tenant `min_threshold` (from
`tenant_configs.scoring_threshold`, configured via the onboarding
wizard). Any lead whose score is below that threshold collapses to
REJECTED regardless of where it would fall on the static scale. This
lets installers dial "aggressivo" (40) vs "elite" (85) without us
having to re-shape the tier curve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...models.enums import LeadScoreTier


@dataclass(frozen=True, slots=True)
class ScoringBreakdown:
    technical: int
    consumption: int
    incentives: int
    solvency: int
    distance: int

    def to_dict(self) -> dict[str, int]:
        return {
            "technical": self.technical,
            "consumption": self.consumption,
            "incentives": self.incentives,
            "solvency": self.solvency,
            "distance": self.distance,
        }


@dataclass(frozen=True, slots=True)
class ScoringWeights:
    technical: float
    consumption: float
    incentives: float
    solvency: float
    distance: float

    @classmethod
    def from_jsonb(cls, data: dict[str, Any] | None) -> "ScoringWeights":
        """Build weights from the `scoring_weights.weights` JSON blob.

        Falls back to the PRD V1 default when fields are missing.
        """
        d = data or {}
        return cls(
            technical=float(d.get("technical", 25)),
            consumption=float(d.get("consumption", 25)),
            incentives=float(d.get("incentives", 15)),
            solvency=float(d.get("solvency", 20)),
            distance=float(d.get("distance", 15)),
        )

    def total(self) -> float:
        return (
            self.technical + self.consumption + self.incentives
            + self.solvency + self.distance
        )


def combine_breakdown(
    breakdown: ScoringBreakdown, weights: ScoringWeights
) -> int:
    """Weighted average → 0..100 integer score.

    If the weights sum to 0 (misconfiguration) we fall back to an equal
    weighting rather than dividing by zero.
    """
    total = weights.total()
    if total <= 0:
        return int(round(
            (breakdown.technical + breakdown.consumption + breakdown.incentives
             + breakdown.solvency + breakdown.distance) / 5.0
        ))
    weighted = (
        breakdown.technical * weights.technical
        + breakdown.consumption * weights.consumption
        + breakdown.incentives * weights.incentives
        + breakdown.solvency * weights.solvency
        + breakdown.distance * weights.distance
    )
    return max(0, min(100, int(round(weighted / total))))


def tier_for(score: int, min_threshold: int | None = None) -> LeadScoreTier:
    """Map the final 0..100 score to a qualitative tier.

    ``min_threshold`` (optional) is the per-tenant floor configured in
    the wizard. When provided, any score strictly below it is forced
    to ``REJECTED`` — even if it would otherwise have landed in COLD
    or WARM. Callers that don't have a tenant context (unit tests,
    back-fills) can omit it for the legacy PRD behaviour.
    """
    if min_threshold is not None and score < min_threshold:
        return LeadScoreTier.REJECTED
    if score > 75:
        return LeadScoreTier.HOT
    if score >= 60:
        return LeadScoreTier.WARM
    if score >= 40:
        return LeadScoreTier.COLD
    return LeadScoreTier.REJECTED
