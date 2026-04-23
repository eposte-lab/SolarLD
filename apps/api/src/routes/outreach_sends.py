"""Outreach sends — paginated history of individual email/postal/WA sends.

Each row in ``outreach_sends`` is one message sent (or attempted) to one
lead. This route surfaces the send history for the dashboard's /invii view.

Previously named ``campaigns.py`` — renamed in migration 0043 to clarify
that individual sends are distinct from acquisition_campaigns (strategic
targeting entities).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client

router = APIRouter()

_SELECT_FIELDS = (
    "id, lead_id, tenant_id, channel, sequence_step, status, "
    "template_id, email_subject, email_message_id, "
    "postal_provider_order_id, postal_tracking_number, "
    "scheduled_for, sent_at, cost_cents, failure_reason, "
    "acquisition_campaign_id, inbox_id, "
    "created_at, updated_at"
)


@router.get("")
async def list_outreach_sends(
    ctx: CurrentUser,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Paginated list of outreach sends for the current tenant.

    Returns newest-first. Use ``limit`` + ``offset`` for pagination.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("outreach_sends")
        .select(_SELECT_FIELDS)
        .eq("tenant_id", tenant_id)
        .order("scheduled_for", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return {"sends": res.data or [], "offset": offset, "limit": limit}
