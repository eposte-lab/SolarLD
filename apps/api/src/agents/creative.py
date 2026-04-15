"""Creative Agent — generates rendering image, transition video, GIF, ROI data.

Pipeline (Sprint 4-5):
 1. Download aerial image from Mapbox Static Images API
 2. Detect roof outline via Claude Vision
 3. Generate "after" image via Replicate (Stable Diffusion + ControlNet)
 4. Post-process with installer logo overlay
 5. Render transition video via Remotion sidecar
 6. Upload all assets to Supabase Storage
 7. Compute ROI data (investment, savings, payback)
"""

from __future__ import annotations

from pydantic import BaseModel

from .base import AgentBase


class CreativeInput(BaseModel):
    tenant_id: str
    lead_id: str


class CreativeOutput(BaseModel):
    image_url: str | None = None
    video_url: str | None = None
    gif_url: str | None = None
    roi_data: dict[str, float] = {}


class CreativeAgent(AgentBase[CreativeInput, CreativeOutput]):
    name = "agent.creative"

    async def execute(self, payload: CreativeInput) -> CreativeOutput:
        # TODO(Sprint 4-5): full rendering pipeline
        return CreativeOutput()
