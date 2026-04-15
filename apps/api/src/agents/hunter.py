"""Hunter Agent — scans a territory and populates the `roofs` table.

V0 stub: the real implementation (Sprint 1-2) integrates Google Solar
API + Mapbox AI fallback. This skeleton defines the contract.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .base import AgentBase


class HunterInput(BaseModel):
    tenant_id: str
    territory_id: str
    max_roofs: int = Field(default=1000, ge=1, le=10000)


class HunterOutput(BaseModel):
    roofs_discovered: int = 0
    roofs_filtered_out: int = 0
    api_cost_cents: int = 0
    used_fallback: bool = False
    next_pagination_token: str | None = None


class HunterAgent(AgentBase[HunterInput, HunterOutput]):
    name = "agent.hunter"

    async def execute(self, payload: HunterInput) -> HunterOutput:
        # TODO(Sprint 1): implement grid sampling + Google Solar API
        # 1) Load territory bbox
        # 2) Generate sampling grid (50m x 50m)
        # 3) Google Solar API buildingInsights:findClosest per point
        # 4) Mapbox fallback when 404
        # 5) Apply technical filters
        # 6) Compute geohash + upsert into roofs
        # 7) Emit roof.scanned events
        await self._emit_event(
            event_type="hunter.scan_stubbed",
            payload={"input": payload.model_dump()},
            tenant_id=payload.tenant_id,
        )
        return HunterOutput()
