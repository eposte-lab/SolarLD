"""Identity Agent — resolves the owner of a roof (B2B/B2C) via cadastral lookup.

Pipeline (Sprint 1-2):
 1. Visura.it: cadastral parcel → intestatario
 2. If P.IVA → Atoka (company details) + Hunter.io (email) + NeverBounce
 3. Else → private citizen (no email, postal only)
 4. Ambiguous cases → Claude classification
 5. Compute pii_hash and check global_blacklist
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..models.enums import SubjectType
from .base import AgentBase


class IdentityInput(BaseModel):
    roof_id: str
    tenant_id: str


class IdentityOutput(BaseModel):
    subject_id: str | None = None
    classification: SubjectType = SubjectType.UNKNOWN
    enrichment_cost_cents: int = 0
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    blacklisted: bool = False


class IdentityAgent(AgentBase[IdentityInput, IdentityOutput]):
    name = "agent.identity"

    async def execute(self, payload: IdentityInput) -> IdentityOutput:
        # TODO(Sprint 1-2): full enrichment pipeline
        return IdentityOutput()
