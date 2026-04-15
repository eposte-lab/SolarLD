"""arq worker definition.

Run with:
    arq src.workers.main.WorkerSettings

Each task is a thin dispatcher around an agent's `run()` method.
"""

from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

from ..agents.compliance import ComplianceAgent, ComplianceInput
from ..agents.creative import CreativeAgent, CreativeInput
from ..agents.hunter import HunterAgent, HunterInput
from ..agents.identity import IdentityAgent, IdentityInput
from ..agents.outreach import OutreachAgent, OutreachInput
from ..agents.scoring import ScoringAgent, ScoringInput
from ..agents.tracking import TrackingAgent, TrackingInput
from ..core.config import settings
from ..core.logging import configure_logging

configure_logging()


async def hunter_task(_ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    out = await HunterAgent().run(HunterInput(**payload))
    return out.model_dump()


async def identity_task(_ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    out = await IdentityAgent().run(IdentityInput(**payload))
    return out.model_dump()


async def scoring_task(_ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    out = await ScoringAgent().run(ScoringInput(**payload))
    return out.model_dump()


async def creative_task(_ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    out = await CreativeAgent().run(CreativeInput(**payload))
    return out.model_dump()


async def outreach_task(_ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    out = await OutreachAgent().run(OutreachInput(**payload))
    return out.model_dump()


async def tracking_task(_ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    out = await TrackingAgent().run(TrackingInput(**payload))
    return out.model_dump()


async def compliance_task(_ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    out = await ComplianceAgent().run(ComplianceInput(**payload))
    return out.model_dump()


class WorkerSettings:
    """arq WorkerSettings class."""

    functions = [
        hunter_task,
        identity_task,
        scoring_task,
        creative_task,
        outreach_task,
        tracking_task,
        compliance_task,
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 600
    keep_result = 3600
