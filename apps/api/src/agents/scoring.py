"""Scoring Agent — computes 0-100 score + tier (hot/warm/cold/rejected).

V1 algorithm:
    score = technical + consumption + incentives + solvency + distance

Weights are loaded from the `scoring_weights` table (active version).
"""

from __future__ import annotations

from pydantic import BaseModel

from ..models.enums import LeadScoreTier
from .base import AgentBase


class ScoringInput(BaseModel):
    tenant_id: str
    roof_id: str
    subject_id: str


class ScoringBreakdown(BaseModel):
    technical: int = 0
    consumption: int = 0
    incentives: int = 0
    solvency: int = 0
    distance: int = 0


class ScoringOutput(BaseModel):
    score: int = 0
    tier: LeadScoreTier = LeadScoreTier.REJECTED
    breakdown: ScoringBreakdown = ScoringBreakdown()


class ScoringAgent(AgentBase[ScoringInput, ScoringOutput]):
    name = "agent.scoring"

    async def execute(self, payload: ScoringInput) -> ScoringOutput:
        # TODO(Sprint 3): load roof + subject + weights, compute each term.
        return ScoringOutput()

    @staticmethod
    def tier_for(score: int) -> LeadScoreTier:
        if score > 75:
            return LeadScoreTier.HOT
        if score >= 60:
            return LeadScoreTier.WARM
        if score >= 40:
            return LeadScoreTier.COLD
        return LeadScoreTier.REJECTED
