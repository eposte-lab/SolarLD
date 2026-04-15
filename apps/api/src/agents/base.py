"""Base class for all agents.

Every agent is a thin async callable with idempotent semantics.
It takes a typed input model, returns a typed output model, and
emits structured audit events to the `events` table.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

TIn = TypeVar("TIn", bound=BaseModel)
TOut = TypeVar("TOut", bound=BaseModel)

log = get_logger(__name__)


class AgentBase(ABC, Generic[TIn, TOut]):
    """Abstract agent base.

    Subclasses must set:
      - name: short identifier for event_source (ex: 'agent.hunter')
      - implement `async execute(input) -> output`
    """

    name: str = "agent.base"

    @abstractmethod
    async def execute(self, payload: TIn) -> TOut:
        """Core agent logic — override in subclasses."""

    async def run(self, payload: TIn) -> TOut:
        """Execute with logging + audit-event emission + error wrapping."""
        log.info(f"{self.name}.start", input=payload.model_dump())
        try:
            result = await self.execute(payload)
        except Exception as exc:
            log.exception(f"{self.name}.error", error=str(exc))
            await self._emit_event(
                event_type=f"{self.name}.error",
                payload={"error": str(exc), "input": payload.model_dump()},
            )
            raise
        log.info(f"{self.name}.success", output=result.model_dump())
        return result

    async def _emit_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        tenant_id: str | None = None,
        lead_id: str | None = None,
    ) -> None:
        """Insert an audit event (best-effort, doesn't fail the agent)."""
        try:
            sb = get_service_client()
            sb.table("events").insert(
                {
                    "tenant_id": tenant_id,
                    "lead_id": lead_id,
                    "event_type": event_type,
                    "event_source": self.name,
                    "payload": payload,
                }
            ).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("event_emit_failed", error=str(exc))
