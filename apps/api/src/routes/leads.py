"""Lead endpoints — primary dashboard surface."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..models.lead import LeadFeedback, LeadListResponse

router = APIRouter()


@router.get("", response_model=LeadListResponse)
async def list_leads(
    ctx: CurrentUser,
    status: str | None = Query(default=None),
    tier: Literal["hot", "warm", "cold", "rejected"] | None = Query(default=None),
    channel: Literal["email", "postal"] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    query = sb.table("leads").select("*", count="exact").eq("tenant_id", tenant_id)
    if status:
        query = query.eq("pipeline_status", status)
    if tier:
        query = query.eq("score_tier", tier)
    if channel:
        query = query.eq("outreach_channel", channel)

    offset = (page - 1) * per_page
    res = (
        query.order("score", desc=True)
        .range(offset, offset + per_page - 1)
        .execute()
    )

    return {
        "data": res.data or [],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": res.count or 0,
        },
    }


@router.get("/{lead_id}")
async def get_lead(ctx: CurrentUser, lead_id: str) -> dict[str, object]:
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("leads")
        .select("*, subjects(*), roofs(*), campaigns(*)")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    return res.data[0]


@router.get("/{lead_id}/timeline")
async def lead_timeline(ctx: CurrentUser, lead_id: str) -> list[dict[str, object]]:
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("events")
        .select("*")
        .eq("lead_id", lead_id)
        .eq("tenant_id", tenant_id)
        .order("occurred_at", desc=True)
        .limit(200)
        .execute()
    )
    return res.data or []


@router.patch("/{lead_id}/feedback")
async def set_feedback(
    ctx: CurrentUser,
    lead_id: str,
    payload: LeadFeedback,
) -> dict[str, object]:
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    update = {
        "feedback": payload.feedback.value,
        "feedback_notes": payload.notes,
        "feedback_at": "now()",
    }
    if payload.contract_value_eur is not None:
        update["contract_value_cents"] = int(payload.contract_value_eur * 100)
    res = (
        sb.table("leads")
        .update(update)
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    return {"ok": True, "data": res.data}


@router.post("/{lead_id}/regenerate-rendering")
async def regen_rendering(ctx: CurrentUser, lead_id: str) -> dict[str, object]:
    """Enqueue a creative-agent re-run for this lead."""
    require_tenant(ctx)
    # TODO: enqueue job → creative agent
    return {"ok": True, "lead_id": lead_id, "job_id": "pending"}
