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
from ..agents.email_extraction import EmailExtractionAgent, EmailExtractionInput
from ..agents.hunter import HunterAgent, HunterInput
from ..agents.outreach import OutreachAgent, OutreachInput
from ..agents.replies import RepliesAgent, RepliesInput
from ..agents.scoring import ScoringAgent, ScoringInput
from ..agents.tracking import TrackingAgent, TrackingInput
from ..core.config import settings
from ..core.logging import configure_logging, get_logger
from ..core.supabase_client import get_service_client
from ..services.b2c_qualify_service import qualify_b2c_lead
from ..services.crm_webhook_service import dispatch_event as crm_dispatch
from ..services.industrial_zones_mapper import map_target_areas_for_tenant
from .cron import (
    cluster_ab_evaluation_cron,
    daily_digest_cron,
    daily_pipeline_cron,
    deliverability_hourly_cron,
    engagement_followup_cron,
    engagement_rollup_cron,
    follow_up_cron,
    practice_deadlines_cron,
    reputation_digest_cron,
    retention_cron,
    send_time_rollup_cron,
    sla_first_touch_cron,
    smartlead_warmup_sync_cron,
    warehouse_cleanup_cron,
    weekly_digest_cron,
)

configure_logging()
log = get_logger(__name__)


async def hunter_task(_ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    out = await HunterAgent().run(HunterInput(**payload))
    return out.model_dump()


async def email_extraction_task(
    _ctx: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Phase 2 (offline filters) + Phase 3 (email extraction + GDPR audit).

    Replaces the legacy identity_task. Enqueued by level4_solar_gate.py
    for every accepted subject. For non-pilot tenants this is a transparent
    pass-through to scoring_task — V2 logic only runs when the tenant has
    pipeline_v2_pilot=true.
    """
    out = await EmailExtractionAgent().run(EmailExtractionInput(**payload))
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


async def practice_generation_task(
    _ctx: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Fan out per-template render tasks for a freshly-created practice.

    Payload:
        {
          "practice_id": "...",
          "tenant_id": "...",
          "template_codes": ["dm_37_08", "comunicazione_comune"]
        }

    Why a separate parent task instead of enqueueing N renders directly
    from the route: the route handler is async and would have to await
    N enqueues sequentially before responding. Pushing fan-out into the
    worker keeps the API's POST response under the 100 ms p95 the
    dashboard expects.
    """
    from ..core.queue import enqueue

    practice_id = payload["practice_id"]
    tenant_id = payload["tenant_id"]
    template_codes = payload.get("template_codes") or []
    enqueued: list[str] = []
    for code in template_codes:
        # Stable job_id makes re-runs idempotent — a second click on
        # "Rigenera all" within the queue window collapses to one job.
        await enqueue(
            "practice_render_document_task",
            {
                "practice_id": practice_id,
                "tenant_id": tenant_id,
                "template_code": code,
            },
            job_id=f"practice-render:{practice_id}:{code}",
        )
        enqueued.append(code)
    return {"practice_id": practice_id, "enqueued": enqueued}


async def practice_render_document_task(
    _ctx: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Render one practice document (one template_code) and persist it.

    WeasyPrint is sync + CPU-heavy. We run the actual render via
    ``asyncio.to_thread`` so the worker can keep its async event loop
    free (pool size = 10, so blocking the loop would starve other jobs).

    On failure we record the error on the document row so the dashboard
    can surface "Rigenera" — and we DON'T re-raise: arq would mark the
    job failed and put it on a dead-letter, but the user-visible state
    is already on the document row.
    """
    import asyncio as _asyncio

    from ..services.practice_service import (
        record_generation_failure,
        render_practice_document,
    )

    practice_id = payload["practice_id"]
    tenant_id = payload["tenant_id"]
    template_code = payload["template_code"]
    try:
        doc = await _asyncio.to_thread(
            render_practice_document,
            practice_id=practice_id,
            template_code=template_code,
            tenant_id=tenant_id,
        )
        return {
            "practice_id": practice_id,
            "template_code": template_code,
            "pdf_url": doc.pdf_url,
            "status": doc.status,
            "generation_error": doc.generation_error,
        }
    except Exception as exc:  # noqa: BLE001 — top-level worker boundary
        record_generation_failure(
            practice_id=practice_id,
            tenant_id=tenant_id,
            template_code=template_code,
            error=f"{type(exc).__name__}: {exc}",
        )
        return {
            "practice_id": practice_id,
            "template_code": template_code,
            "status": "draft",
            "generation_error": str(exc),
        }


async def extract_practice_upload_task(
    _ctx: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Run Claude Vision OCR over a practice_uploads row.

    Payload:
        { "upload_id": "...", "tenant_id": "..." }

    Behaviour:
        1. Load the practice_uploads row + storage bytes via service-role
           client (the row was inserted before the job was queued, so it
           always exists barring a manual race).
        2. Call extract_for_kind() — never raises on parse failure.
        3. UPDATE the row with extraction_status / extracted_data /
           confidence / extraction_error / extracted_at.

    Errors here are logged but never re-raised: the row's
    extraction_status='failed' surfaces the issue in the dashboard, and
    re-raising would pile retries on a determinism-bound failure
    (a corrupt PDF retried 3× still fails).
    """
    from ..core.supabase_client import get_service_client
    from ..services.practice_extraction_service import extract_for_kind

    upload_id = payload["upload_id"]
    sb = get_service_client()

    row_res = (
        sb.table("practice_uploads").select("*").eq("id", upload_id).execute()
    )
    rows = row_res.data or []
    if not rows:
        log.warning(
            "practice.upload.extract.row_missing", upload_id=upload_id
        )
        return {"upload_id": upload_id, "ok": False, "error": "row_missing"}

    row = rows[0]
    storage_path = row["storage_path"]
    upload_kind = row["upload_kind"]
    mime_type = row["mime_type"]

    # Download the file bytes from the private bucket via service role.
    try:
        file_bytes = sb.storage.from_("practice-uploads").download(
            storage_path
        )
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "practice.upload.extract.download_failed",
            upload_id=upload_id,
            path=storage_path,
        )
        sb.table("practice_uploads").update(
            {
                "extraction_status": "failed",
                "extraction_error": f"download_failed:{type(exc).__name__}",
                "extracted_at": "now()",
            }
        ).eq("id", upload_id).execute()
        return {"upload_id": upload_id, "ok": False, "error": "download_failed"}

    # Run the OCR.
    try:
        result = await extract_for_kind(
            file_bytes, mime_type, upload_kind
        )
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "practice.upload.extract.api_failed", upload_id=upload_id
        )
        sb.table("practice_uploads").update(
            {
                "extraction_status": "failed",
                "extraction_error": f"api_failed:{type(exc).__name__}",
                "extracted_at": "now()",
            }
        ).eq("id", upload_id).execute()
        return {"upload_id": upload_id, "ok": False, "error": "api_failed"}

    # Persist outcome.
    if not result.success:
        update = {
            "extraction_status": "failed",
            "extraction_error": result.error,
            "raw_response": result.raw_response,
            "extracted_at": "now()",
        }
    else:
        update = {
            "extraction_status": (
                "manual_required" if result.manual_required else "success"
            ),
            "extracted_data": result.fields,
            "confidence": result.confidence,
            "raw_response": result.raw_response,
            "extracted_at": "now()",
            "extraction_error": None,
        }

    sb.table("practice_uploads").update(update).eq("id", upload_id).execute()
    log.info(
        "practice.upload.extract.complete",
        upload_id=upload_id,
        kind=upload_kind,
        success=result.success,
        confidence=result.confidence,
    )
    return {
        "upload_id": upload_id,
        "ok": result.success,
        "confidence": result.confidence,
        "manual_required": result.manual_required,
    }


async def map_target_areas_task(
    _ctx: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """L0 — One-shot OSM zone mapping for a tenant.

    Triggered via POST /v1/territory/map (or onboarding completion).
    Slow (5-15 min for 2-3 provinces) so always runs as ARQ background
    job. Idempotent: re-running upserts existing zones by
    (tenant_id, osm_type, osm_id), so the operator can re-map after
    changing wizard_groups or province_codes.

    Payload schema:
      tenant_id: str (UUID)
      wizard_groups: list[str]
      province_codes: list[str]  # Italian ISO 3166-2 suffixes (BS, BG, ...)
    """
    sb = get_service_client()
    result = await map_target_areas_for_tenant(
        sb,
        tenant_id=payload["tenant_id"],
        wizard_groups=list(payload.get("wizard_groups") or []),
        province_codes=list(payload.get("province_codes") or []),
    )
    return {
        "tenant_id": result.tenant_id,
        "fetched": result.total_zones_fetched,
        "matched": result.zones_matched_to_sectors,
        "persisted": result.zones_persisted,
        "sectors_covered": result.sectors_covered,
        "provinces_covered": result.provinces_covered,
        "elapsed_s": result.elapsed_seconds,
        "endpoint": result.overpass_endpoint_used,
        "errors": result.errors,
    }


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
        email_extraction_task,
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
        practice_generation_task,
        practice_render_document_task,
        extract_practice_upload_task,
        map_target_areas_task,
    ]
    # Scheduled jobs (UTC):
    #   :00 every hour   → deliverability_hourly_cron   (bounce/complaint spike check)
    #   02:30 every day  → reputation_digest_cron       (refresh domain_reputation)
    #   03:15 every day  → retention_cron               (GDPR 24-month purge)
    #   03:30 every day  → cluster_ab_evaluation_cron   (Sprint 9: promote A/B winners)
    #   03:45 every day  → send_time_rollup_cron        (per-lead best UTC hour)
    #   04:00 every day  → engagement_rollup_cron       (portal heat → leads)
    #   06:00 every day  → smartlead_warmup_sync_cron   (inbox health + warmup caps)
    #   07:00 every day  → daily_digest_cron            (opt-in feature flag)
    #   07:30 every day  → follow_up_cron               (reads best_send_hour)
    #   08:00 Mon        → weekly_digest_cron           (opt-in feature flag)
    #   08:30 every day  → sla_first_touch_cron         (notify overdue leads)
    cron_jobs = [
        # Task 15: hourly deliverability guard — catch domain spikes fast.
        cron(deliverability_hourly_cron, minute=0, run_at_startup=False),
        cron(reputation_digest_cron, hour=2, minute=30, run_at_startup=False),
        # Sprint 11: warehouse expiry sweep BEFORE the daily pipeline,
        # so today's pick doesn't see leads that should have been
        # expired (which would otherwise still satisfy the
        # `expires_at > now()` guard inside warehouse_pick by a few
        # minutes around midnight).
        cron(warehouse_cleanup_cron, hour=3, minute=0, run_at_startup=False),
        cron(retention_cron, hour=3, minute=15, run_at_startup=False),
        # Sprint 9 B.5: cluster A/B chi-square evaluation + auto-promotion.
        cron(cluster_ab_evaluation_cron, hour=3, minute=30, run_at_startup=False),
        # Sprint 11: per-tenant warehouse refill + FIFO pick of today's
        # send batch. Runs after the cleanup sweep + A/B evaluation so
        # each tenant's daily quota is dispatched against fresh assignments.
        cron(daily_pipeline_cron, hour=5, minute=30, run_at_startup=False),
        cron(send_time_rollup_cron, hour=3, minute=45, run_at_startup=False),
        cron(engagement_rollup_cron, hour=4, minute=0, run_at_startup=False),
        # Task 14: sync Smartlead warm-up health scores before the morning
        # outreach run so inbox_service.pick_and_claim has fresh caps.
        cron(smartlead_warmup_sync_cron, hour=6, minute=0, run_at_startup=False),
        cron(daily_digest_cron, hour=7, minute=0, run_at_startup=False),
        cron(follow_up_cron, hour=7, minute=30, run_at_startup=False),
        cron(
            weekly_digest_cron,
            weekday=0,  # Monday
            hour=8,
            minute=0,
            run_at_startup=False,
        ),
        # Sprint 10: engagement-based follow-up scenarios.
        cron(engagement_followup_cron, hour=8, minute=15, run_at_startup=False),
        cron(sla_first_touch_cron, hour=8, minute=30, run_at_startup=False),
        # Livello 2 Sprint 1: scan practice_deadlines for newly-overdue
        # rows once a day.  Runs after the morning outreach burst so
        # the bell isn't competing with delivery noise; UTC 09:00 ≈
        # 10/11 Italian local — practical for the installer to action.
        cron(practice_deadlines_cron, hour=9, minute=0, run_at_startup=False),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 600
    keep_result = 3600
