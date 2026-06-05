"""Outreach sends — paginated history of individual email/postal/WA sends.

Each row in ``outreach_sends`` is one message sent (or attempted) to one
lead. This route surfaces the send history for the dashboard's /invii view.

Previously named ``campaigns.py`` — renamed in migration 0043 to clarify
that individual sends are distinct from acquisition_campaigns (strategic
targeting entities).
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import Response

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client

router = APIRouter()
log = get_logger(__name__)

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


_EXPORT_EMBED = (
    "channel, sequence_step, status, email_subject, sent_at, scheduled_for, failure_reason, "
    "leads:leads("
    "pipeline_status, outreach_delivered_at, outreach_opened_at, outreach_clicked_at, "
    "subjects:subjects(business_name, decision_maker_name, decision_maker_email, decision_maker_phone), "
    "roofs:roofs(comune, provincia)"
    ")"
)

_EXPORT_HEADERS = [
    "Ragione sociale",
    "Referente",
    "Email",
    "Telefono",
    "Comune",
    "Provincia",
    "Canale",
    "Step",
    "Oggetto",
    "Stato",
    "Data invio",
    "Consegnata",
    "Aperta",
    "Cliccata",
    "Stato pipeline",
    "Errore",
]


def _embed_one(value: Any) -> dict[str, Any]:
    """PostgREST returns an embedded to-one relation as an object, but the
    generated client sometimes widens it to a single-element list — accept
    both and return a dict (or empty dict)."""
    if isinstance(value, list):
        return value[0] if value and isinstance(value[0], dict) else {}
    return value if isinstance(value, dict) else {}


@router.get("/export.csv")
async def export_outreach_sends_csv(ctx: CurrentUser) -> Response:
    """Download EVERY outreach send for the current tenant as a CSV.

    One row per send (newest first), joined with the lead's engagement
    timestamps and the subject's contact details — a complete "chi abbiamo
    contattato" sheet for CRM import or hand-off. Excel-friendly: ``;``
    delimiter + UTF-8 BOM so accented names render correctly.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # Fetch ALL sends (paginated past the PostgREST 1000-row cap) so the export
    # is never silently truncated.
    rows: list[dict[str, Any]] = []
    page = 0
    page_size = 1000
    while True:
        res = (
            sb.table("outreach_sends")
            .select(_EXPORT_EMBED)
            .eq("tenant_id", tenant_id)
            .order("sent_at", desc=True)
            .range(page * page_size, page * page_size + page_size - 1)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        page += 1

    def _flag(value: Any) -> str:
        return "Sì" if value else ""

    def _dt(value: Any) -> str:
        return str(value)[:16].replace("T", " ") if value else ""

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(_EXPORT_HEADERS)
    for r in rows:
        lead = _embed_one(r.get("leads"))
        subj = _embed_one(lead.get("subjects"))
        roof = _embed_one(lead.get("roofs"))
        writer.writerow(
            [
                subj.get("business_name") or "",
                subj.get("decision_maker_name") or "",
                subj.get("decision_maker_email") or "",
                subj.get("decision_maker_phone") or "",
                roof.get("comune") or "",
                roof.get("provincia") or "",
                r.get("channel") or "",
                r.get("sequence_step") if r.get("sequence_step") is not None else "",
                r.get("email_subject") or "",
                r.get("status") or "",
                _dt(r.get("sent_at") or r.get("scheduled_for")),
                _flag(lead.get("outreach_delivered_at")),
                _flag(lead.get("outreach_opened_at")),
                _flag(lead.get("outreach_clicked_at")),
                lead.get("pipeline_status") or "",
                r.get("failure_reason") or "",
            ]
        )

    # UTF-8 BOM → Excel opens accented text correctly.
    content = ("﻿" + buf.getvalue()).encode("utf-8")
    buf.close()
    today = datetime.now(UTC).strftime("%Y%m%d")
    log.info("outreach_sends.export_csv", tenant_id=str(tenant_id), rows=len(rows))
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="outreach_{today}.csv"'},
    )
