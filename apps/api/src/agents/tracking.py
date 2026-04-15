"""Tracking Agent — consumes webhook events and updates lead pipeline_status.

Subscribes to:
 - Resend (email events)
 - Pixartprinting (postal events)
 - 360dialog (WhatsApp inbound)
 - Stripe (billing events)
 - Lead Portal (own page views / CTA clicks)

Emits Supabase Realtime updates so the dashboard reacts in real time.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .base import AgentBase


class TrackingInput(BaseModel):
    provider: str
    event_type: str
    raw_payload: dict[str, Any]


class TrackingOutput(BaseModel):
    processed: bool = True
    lead_id: str | None = None
    new_status: str | None = None


class TrackingAgent(AgentBase[TrackingInput, TrackingOutput]):
    name = "agent.tracking"

    async def execute(self, payload: TrackingInput) -> TrackingOutput:
        # TODO(Sprint 6-9): normalize provider events, update lead pipeline_status
        return TrackingOutput()
