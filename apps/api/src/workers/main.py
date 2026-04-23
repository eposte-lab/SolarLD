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
from ..services.b2c_qualify_service import qualify_b2c_lead
from ..services.crm_webhook_service import dispatch_event as crm_dispatch
from .cron import (
    daily_digest_cron,
    engagement_rollup_cron,
    follow_up_cron,
    reputation_digest_cron,
    retention_cron,
    send_time_rollup_cron,
    sla_first_touch_cron,
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


async def b2c_post_engagement_qualify_task(
    _ctx: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Enqueued when a B2C lead signals positive intent (Meta form
    submission, email reply with positive sentiment, WhatsApp
    engagement). Runs Mapbox + Solar to attach a roof to the lead.

    Payload: ``{"tenant_id": str, "lead_id": str}``.
    """
    return await qualify_b2c_lead(
        tenant_id=payload["tenant_id"],
        lead_id=payload["lead_id"],
    )


async def meta_lead_enrich_task(
    _ctx: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Fetch Meta Graph API field_data for a newly-received leadgen id.

    Today this is a stub that records the intent — the real Graph
    call lands in Phase 4 once Meta app review is complete. The stub
    path is important so the webhook enqueues a deterministic task
    id per leadgen and we have a marker to backfill from later.
    """
    from ..core.supabase_client import get_service_client

    tenant_id = payload["tenant_id"]
    leadgen_id = payload["leadgen_id"]
    sb = get_service_client()
    sb.table("leads").update(
        {
            "inbound_payload": {
                "leadgen_id": leadgen_id,
                "enrich_pending": True,
            }
        }
    ).eq("tenant_id", tenant_id).eq("meta_lead_id", leadgen_id).execute()
    return {"status": "pending_graph_call", "leadgen_id": leadgen_id}


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
        b2c_post_engagement_qualify_task,
        meta_lead_enrich_task,
    ]
    # Scheduled jobs (UTC):
    #   02:30 every day  → reputation_digest_cron  (refresh domain_reputation)
    #   03:15 every day  → retention_cron          (GDPR 24-month purge)
    #   03:45 every day  → send_time_rollup_cron   (per-lead best UTC hour)
    #   04:00 every day  → engagement_rollup_cron  (portal heat → leads)
    #   07:00 every day  → daily_digest_cron       (opt-in feature flag)
    #   07:30 every day  → follow_up_cron          (reads best_send_hour)
    #   08:00 Mon        → weekly_digest_cron      (opt-in feature flag)
    #   08:30 every day  → sla_first_touch_cron    (notify overdue leads)
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
        cron(sla_first_touch_cron, hour=8, minute=30, run_at_startup=False),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 600
    keep_result = 3600
