"""arq worker definition.

Run with:
    arq src.workers.main.WorkerSettings

Each task is a thin dispatcher around an agent's `run()` method.
"""

from __future__ import annotations

from typing import Any

from arq import cron
from arq.connections import RedisSettings

from ..agents.compliance import ComplianceAgent, ComplianceInput
from ..agents.conversation import ConversationAgent, ConversationInput
from ..agents.creative import CreativeAgent, CreativeInput
from ..agents.hunter import HunterAgent, HunterInput
from ..agents.identity import IdentityAgent, IdentityInput
from ..agents.outreach import OutreachAgent, OutreachInput
from ..agents.replies import RepliesAgent, RepliesInput
from ..agents.scoring import ScoringAgent, ScoringInput
from ..agents.tracking import TrackingAgent, TrackingInput
from ..core.config import settings
from ..core.logging import configure_logging
from ..services.crm_webhook_service import dispatch_event as crm_dispatch
from .cron import (
    daily_digest_cron,
    engagement_rollup_cron,
    follow_up_cron,
    reputation_digest_cron,
    retention_cron,
    send_time_rollup_cron,
    weekly_digest_cron,
)

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


async def replies_task(_ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    out = await RepliesAgent().run(RepliesInput(**payload))
    return out.model_dump()


async def conversation_task(
    _ctx: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    out = await ConversationAgent().run(ConversationInput(**payload))
    return out.model_dump()


async def crm_webhook_task(
    _ctx: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Fan out a lifecycle event to every active subscription.

    Payload shape:
        {
          "tenant_id": "...",
          "event_type": "lead.scored",
          "occurred_at": "2026-04-18T12:34:56Z",
          "data": { ... }
        }
    """
    return await crm_dispatch(
        tenant_id=payload["tenant_id"],
        event_type=payload["event_type"],
        occurred_at=payload["occurred_at"],
        data=payload.get("data", {}),
    )


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
        replies_task,
        conversation_task,
        crm_webhook_task,
    ]
    # Scheduled jobs (UTC):
    #   02:30 every day  → reputation_digest_cron  (refresh domain_reputation)
    #   03:15 every day  → retention_cron          (GDPR 24-month purge)
    #   03:45 every day  → send_time_rollup_cron   (per-lead best UTC hour)
    #   04:00 every day  → engagement_rollup_cron  (portal heat → leads)
    #   07:00 every day  → daily_digest_cron       (opt-in feature flag)
    #   07:30 every day  → follow_up_cron          (reads best_send_hour)
    #   08:00 Mon        → weekly_digest_cron      (opt-in feature flag)
    cron_jobs = [
        cron(reputation_digest_cron, hour=2, minute=30, run_at_startup=False),
        cron(retention_cron, hour=3, minute=15, run_at_startup=False),
        cron(send_time_rollup_cron, hour=3, minute=45, run_at_startup=False),
        cron(engagement_rollup_cron, hour=4, minute=0, run_at_startup=False),
        cron(daily_digest_cron, hour=7, minute=0, run_at_startup=False),
        cron(follow_up_cron, hour=7, minute=30, run_at_startup=False),
        cron(
            weekly_digest_cron,
            weekday=0,  # Monday
            hour=8,
            minute=0,
            run_at_startup=False,
        ),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 600
    keep_result = 3600
