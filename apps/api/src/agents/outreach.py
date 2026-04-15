"""Outreach Agent — sends first-contact email (B2B) or postcard (B2C).

Branch A (email B2B):
 - Claude generates 1-sentence personalized opener
 - MJML template → Resend send
 - Schedule 3-step follow-up sequence

Branch B (postal B2C):
 - Generate PDF postcard (rendering + ROI + QR + opt-out)
 - POST to Pixartprinting Direct Mail API
"""

from __future__ import annotations

from pydantic import BaseModel

from ..models.enums import OutreachChannel
from .base import AgentBase


class OutreachInput(BaseModel):
    tenant_id: str
    lead_id: str
    channel: OutreachChannel


class OutreachOutput(BaseModel):
    campaign_id: str | None = None
    provider_id: str | None = None
    scheduled_for: str | None = None
    cost_cents: int = 0


class OutreachAgent(AgentBase[OutreachInput, OutreachOutput]):
    name = "agent.outreach"

    async def execute(self, payload: OutreachInput) -> OutreachOutput:
        # TODO(Sprint 6-8): full outreach pipeline
        return OutreachOutput()
