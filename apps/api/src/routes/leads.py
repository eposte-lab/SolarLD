"""Lead endpoints — primary dashboard surface."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.queue import enqueue, fire_crm_event
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..models.lead import LeadFeedback, LeadListResponse
from ..services.audit_service import log_action as audit_log

log = get_logger(__name__)

router = APIRouter()

# Columns exported to CSV — flat, CRM-friendly shape. Nested
# subject/roof fields are lifted to the top level with a prefix so
# Excel and Salesforce/HubSpot importers map them cleanly.
_CSV_COLUMNS: list[str] = [
    "lead_id",
    "public_slug",
    "pipeline_status",
    "score",
    "score_tier",
    "feedback",
    "contract_value_eur",
    "created_at",
    "outreach_channel",
    "outreach_sent_at",
    "outreach_delivered_at",
    "outreach_opened_at",
    "outreach_clicked_at",
    "dashboard_visited_at",
    "whatsapp_initiated_at",
    "subject_type",
    "business_name",
    "owner_first_name",
    "owner_last_name",
    "decision_maker_email",
    "decision_maker_email_verified",
    "ateco_code",
    "partita_iva",
    "roof_address",
    "roof_comune",
    "roof_provincia",
    "roof_cap",
    "roof_area_sqm",
    "estimated_kwp",
    "estimated_yearly_kwh",
]


def _flatten(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten a lead row (with nested subject/roof objects) for CSV."""
    subj = row.get("subjects") or {}
    roof = row.get("roofs") or {}
    if isinstance(subj, list):
        subj = subj[0] if subj else {}
    if isinstance(roof, list):
        roof = roof[0] if roof else {}

    contract_cents = row.get("contract_value_cents")
    return {
        "lead_id": row.get("id"),
        "public_slug": row.get("public_slug"),
        "pipeline_status": row.get("pipeline_status"),
        "score": row.get("score"),
        "score_tier": row.get("score_tier"),
        "feedback": row.get("feedback"),
        "contract_value_eur": (
            f"{contract_cents / 100:.2f}" if contract_cents else ""
        ),
        "created_at": row.get("created_at"),
        "outreach_channel": row.get("outreach_channel"),
        "outreach_sent_at": row.get("outreach_sent_at"),
        "outreach_delivered_at": row.get("outreach_delivered_at"),
        "outreach_opened_at": row.get("outreach_opened_at"),
        "outreach_clicked_at": row.get("outreach_clicked_at"),
        "dashboard_visited_at": row.get("dashboard_visited_at"),
        "whatsapp_initiated_at": row.get("whatsapp_initiated_at"),
        "subject_type": subj.get("type"),
        "business_name": subj.get("business_name"),
        "owner_first_name": subj.get("owner_first_name"),
        "owner_last_name": subj.get("owner_last_name"),
        "decision_maker_email": subj.get("decision_maker_email"),
        "decision_maker_email_verified": subj.get(
            "decision_maker_email_verified"
        ),
        "ateco_code": subj.get("ateco_code"),
        "partita_iva": subj.get("partita_iva"),
        "roof_address": roof.get("address"),
        "roof_comune": roof.get("comune"),
        "roof_provincia": roof.get("provincia"),
        "roof_cap": roof.get("cap"),
        "roof_area_sqm": roof.get("area_sqm"),
        "estimated_kwp": roof.get("estimated_kwp"),
        "estimated_yearly_kwh": roof.get("estimated_yearly_kwh"),
    }


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


@router.get("/hot")
async def list_hot_leads(
    ctx: CurrentUser,
    since_hours: int = Query(default=72, ge=1, le=720),
    min_score: int = Query(default=60, ge=0, le=100),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, object]:
    """Real-time "Caldi adesso" — leads who showed engagement recently.

    Sprint 8 Fase C.2.

    Returns leads ordered by ``engagement_score DESC, last_portal_event_at
    DESC`` filtered by:
      * tenant_id (RLS via service client + explicit eq)
      * ``engagement_score >= min_score``
      * ``last_portal_event_at >= now() - since_hours``
      * pipeline NOT IN closed/responded states (engaged, appointment,
        whatsapp, closed_won, closed_lost, blacklisted) — the operator
        wants the ones who are *interested but haven't replied*.

    The dashboard "Caldi adesso" tab calls this on poll (no realtime
    sub on this endpoint — the bump function on /portal/track gives a
    sub-second feedback loop already; the operator just refreshes).
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # Cut-off: only consider leads who fired a portal event within
    # the window. Without the cut-off, an old lead with score=80
    # from a month ago would camp the top of the list forever.
    from datetime import timedelta

    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=since_hours)
    ).isoformat()

    # Pipeline statuses that mean the lead has *already* moved past
    # the "needs follow-up" point. We want the gap between
    # high-engagement and not-yet-responded.
    excluded_statuses = (
        "engaged",
        "whatsapp",
        "appointment",
        "closed_won",
        "closed_lost",
        "blacklisted",
    )

    query = (
        sb.table("leads")
        .select(
            "id, public_slug, score, score_tier, engagement_score, "
            "engagement_score_updated_at, last_portal_event_at, "
            "pipeline_status, outreach_sent_at, "
            "subjects(type, business_name, owner_first_name, "
            "owner_last_name, decision_maker_email), "
            "roofs(address, comune, provincia, estimated_kwp)"
        )
        .eq("tenant_id", tenant_id)
        .gte("engagement_score", min_score)
        .gte("last_portal_event_at", cutoff)
        # ``not_.in_`` is the supabase-py shape for NOT IN.
        .not_.in_("pipeline_status", list(excluded_statuses))
        .order("engagement_score", desc=True)
        .order("last_portal_event_at", desc=True)
        .limit(limit)
    )
    res = query.execute()
    return {
        "data": res.data or [],
        "filters": {
            "since_hours": since_hours,
            "min_score": min_score,
            "limit": limit,
        },
    }


@router.get("/export.csv")
async def export_leads_csv(
    ctx: CurrentUser,
    status: str | None = Query(default=None),
    tier: Literal["hot", "warm", "cold", "rejected"] | None = Query(default=None),
    channel: Literal["email", "postal"] | None = Query(default=None),
    feedback: str | None = Query(default=None),
    max_rows: int = Query(default=5000, ge=1, le=50000),
) -> StreamingResponse:
    """Export the caller's leads as CSV — data egress for CRM imports.

    Filters mirror ``GET /v1/leads`` so the operator can narrow to
    "HOT leads signed this quarter" before dropping the file into
    Salesforce / HubSpot. The response streams row-by-row to avoid
    buffering the whole export in memory on large tenants.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    query = (
        sb.table("leads")
        .select(
            "id, public_slug, pipeline_status, score, score_tier, feedback, "
            "contract_value_cents, created_at, outreach_channel, "
            "outreach_sent_at, outreach_delivered_at, outreach_opened_at, "
            "outreach_clicked_at, dashboard_visited_at, whatsapp_initiated_at, "
            "subjects(type, business_name, owner_first_name, owner_last_name, "
            "decision_maker_email, decision_maker_email_verified, ateco_code, "
            "partita_iva), "
            "roofs(address, comune, provincia, cap, area_sqm, estimated_kwp, "
            "estimated_yearly_kwh)"
        )
        .eq("tenant_id", tenant_id)
    )
    if status:
        query = query.eq("pipeline_status", status)
    if tier:
        query = query.eq("score_tier", tier)
    if channel:
        query = query.eq("outreach_channel", channel)
    if feedback:
        query = query.eq("feedback", feedback)

    res = query.order("score", desc=True).limit(max_rows).execute()
    rows = res.data or []

    def _generate() -> Any:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        yield buf.getvalue()
        for row in rows:
            buf.seek(0)
            buf.truncate(0)
            writer.writerow(_flatten(row))
            yield buf.getvalue()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"solarlead-leads-{stamp}.csv"
    return StreamingResponse(
        _generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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

    # Fan out contract_signed to any registered CRM endpoints AND drop
    # an in-app notification — this is the single most important
    # lifecycle event, so we dispatch straight from the route instead
    # of waiting for an agent to emit it.
    if payload.feedback.value == "contract_signed":
        try:
            await fire_crm_event(
                tenant_id=tenant_id,
                event_type="lead.contract_signed",
                data={
                    "lead_id": lead_id,
                    "contract_value_cents": update.get("contract_value_cents"),
                    "notes": payload.notes,
                },
            )
        except Exception:  # noqa: BLE001
            # Never fail the feedback write because of webhook plumbing.
            pass

        try:
            from ..services.notifications_service import notify

            contract_eur = (
                f"€{(update['contract_value_cents'] or 0) / 100:,.0f}"
                if update.get("contract_value_cents")
                else None
            )
            await notify(
                tenant_id=tenant_id,
                title="Contratto firmato",
                body=(
                    f"Nuovo contratto da {contract_eur}"
                    if contract_eur
                    else "Un lead ha firmato il contratto."
                ),
                severity="success",
                href=f"/leads/{lead_id}",
                metadata={"lead_id": lead_id},
            )
        except Exception:  # noqa: BLE001
            pass

    await audit_log(
        tenant_id,
        "lead.feedback_updated",
        actor_user_id=ctx.sub,
        target_table="leads",
        target_id=lead_id,
        diff={
            "feedback": payload.feedback.value,
            "notes": payload.notes,
            "contract_value_eur": payload.contract_value_eur,
        },
    )
    return {"ok": True, "data": res.data}


@router.delete("/{lead_id}", status_code=204)
async def delete_lead(
    ctx: CurrentUser,
    lead_id: str,
) -> Response:
    """Hard-delete a lead and all cascaded rows (GDPR right to be forgotten).

    The schema has ON DELETE CASCADE on every child table
    (subjects, roofs, campaigns, events, portal_events, conversions,
    crm_webhook_deliveries), so deleting the ``leads`` row is sufficient
    to erase all PII associated with this lead.

    The action is recorded in ``audit_log`` before deletion so the
    operator can prove compliance even after the row is gone.
    Irreversible — confirm in the dashboard before calling.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # Verify ownership before deleting
    res = (
        sb.table("leads")
        .select("id, subjects(owner_first_name, owner_last_name, business_name)")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead_row = res.data[0]
    subj = lead_row.get("subjects") or {}
    if isinstance(subj, list):
        subj = subj[0] if subj else {}

    # Audit BEFORE deletion so the row still exists during the write
    await audit_log(
        tenant_id,
        "lead.deleted",
        actor_user_id=ctx.sub,
        target_table="leads",
        target_id=lead_id,
        diff={
            "business_name": subj.get("business_name"),
            "owner": " ".join(
                filter(
                    None,
                    [subj.get("owner_first_name"), subj.get("owner_last_name")],
                )
            ) or None,
            "reason": "gdpr_erasure",
        },
    )

    sb.table("leads").delete().eq("id", lead_id).execute()
    return Response(status_code=204)


@router.post("/{lead_id}/regenerate-rendering")
async def regen_rendering(
    ctx: CurrentUser,
    lead_id: str,
    force: bool = Query(
        default=True,
        description=(
            "When true (default), re-render even if a previous run already "
            "produced an after-image. Pass force=false to make the job a "
            "no-op when rendering_image_url is already set."
        ),
    ),
) -> dict[str, object]:
    """Enqueue a Creative-Agent re-run for this lead.

    Used by the dashboard after a tenant updates their brand colour,
    after a roof's geometry is corrected, or when the tenant wants to
    retry a prior failed rendering. The job is idempotent per lead via
    its deterministic job_id, so double-clicks collapse into one run.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("leads")
        .select("id")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    job = await enqueue(
        "creative_task",
        {"tenant_id": tenant_id, "lead_id": lead_id, "force": force},
        job_id=f"creative:{tenant_id}:{lead_id}",
    )
    return {"ok": True, "lead_id": lead_id, **job}


@router.post("/{lead_id}/rescore")
async def rescore_lead(ctx: CurrentUser, lead_id: str) -> dict[str, object]:
    """Re-run the Scoring Agent for a single lead.

    Useful after the tenant tweaks HQ coords, after a new ATECO profile is
    added, or when regional incentives are refreshed by the weekly scraper.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("leads")
        .select("id, roof_id, subject_id")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead = res.data[0]
    job = await enqueue(
        "scoring_task",
        {
            "tenant_id": tenant_id,
            "roof_id": lead["roof_id"],
            "subject_id": lead["subject_id"],
        },
        job_id=f"scoring:{tenant_id}:{lead['roof_id']}:{lead['subject_id']}",
    )
    return {"ok": True, "lead_id": lead_id, **job}


@router.post("/{lead_id}/send-outreach")
async def send_outreach(
    ctx: CurrentUser,
    lead_id: str,
    channel: Literal["email", "postal"] = Query(default="email"),
    force: bool = Query(
        default=False,
        description=(
            "Re-send the outreach even if outreach_sent_at is already set. "
            "Default false so accidental double-clicks collapse into one job."
        ),
    ),
) -> dict[str, object]:
    """Enqueue an Outreach-Agent run for one lead (Sprint 6: email only).

    Idempotent per lead+channel via the deterministic job_id, so
    double-clicks on the dashboard collapse to a single worker run.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("leads")
        .select("id")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    job = await enqueue(
        "outreach_task",
        {
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "channel": channel,
            "force": force,
        },
        job_id=f"outreach:{tenant_id}:{lead_id}:{channel}",
    )
    return {"ok": True, "lead_id": lead_id, **job}


# ---------------------------------------------------------------------------
# Follow-up drafter — Part B.9
# ---------------------------------------------------------------------------
# Two-step flow: generate draft → (user edits in dashboard) → send.
# Draft generation calls Claude with the full lead context so the copy
# is concrete (ROI numbers, first name, engagement signals). The
# send-draft endpoint bypasses OutreachAgent's template system and
# goes straight to Resend + campaigns table, so the sequence log stays
# accurate.


class FollowUpDraftResponse(BaseModel):
    lead_id: str
    subject: str
    body: str


class SendDraftRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=8000)


class SendDraftResponse(BaseModel):
    ok: bool
    campaign_id: str
    message_id: str | None = None


@router.post("/{lead_id}/draft-followup", response_model=FollowUpDraftResponse)
async def draft_followup(
    ctx: CurrentUser,
    lead_id: str,
) -> FollowUpDraftResponse:
    """Generate a personalised follow-up draft using Claude.

    Gathers the full lead context — subject, ROI, engagement signals,
    campaign history, recent events — and feeds it to Claude to produce
    a {subject, body} JSON. The draft is returned as-is; the dashboard
    shows it in an editable textarea before the operator sends it.

    Tier-gated: requires advanced_analytics (Pro+).
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # Auth-scope + tier gate
    from ..core.tier import Capability, require_capability
    from ..services.claude_service import complete_json

    lead_res = (
        sb.table("leads")
        .select(
            "id, tenant_id, pipeline_status, score, score_tier, "
            "outreach_sent_at, outreach_opened_at, outreach_clicked_at, "
            "dashboard_visited_at, whatsapp_initiated_at, "
            "engagement_score, portal_sessions, portal_total_time_sec, "
            "deepest_scroll_pct, feedback, feedback_notes, roi_data, "
            "subjects(type, business_name, owner_first_name, owner_last_name, "
            "decision_maker_email), "
            "roofs(address, comune, provincia, cap, estimated_kwp, "
            "estimated_yearly_kwh, area_sqm)"
        )
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    if not lead_res.data:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead = lead_res.data[0]
    tenant_res = (
        sb.table("tenants")
        .select("tier, settings, business_name, email_from_domain, email_from_name")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    tenant = (tenant_res.data or [{}])[0]

    # Tier gate — advanced_analytics = Pro+
    require_capability(tenant, Capability.ADVANCED_ANALYTICS)

    # Recent campaigns (last 5)
    cmp_res = (
        sb.table("outreach_sends")
        .select("sequence_step, status, sent_at, channel, email_subject, failure_reason")
        .eq("lead_id", lead_id)
        .order("sent_at", desc=True)
        .limit(5)
        .execute()
    )
    campaigns = cmp_res.data or []

    # Recent events (last 10)
    evt_res = (
        sb.table("events")
        .select("event_type, occurred_at")
        .eq("lead_id", lead_id)
        .order("occurred_at", desc=True)
        .limit(10)
        .execute()
    )
    events = evt_res.data or []

    # Build context for Claude
    subj = lead.get("subjects") or {}
    if isinstance(subj, list):
        subj = subj[0] if subj else {}
    roof = lead.get("roofs") or {}
    if isinstance(roof, list):
        roof = roof[0] if roof else {}
    roi = lead.get("roi_data") or {}

    business = subj.get("business_name") or ""
    first_name = subj.get("owner_first_name") or ""
    last_name = subj.get("owner_last_name") or ""
    person = f"{first_name} {last_name}".strip() or "il cliente"
    subject_type = subj.get("type") or "unknown"
    comune = roof.get("comune") or ""
    provincia = roof.get("provincia") or ""
    kwp = roi.get("estimated_kwp") or roof.get("estimated_kwp")
    savings = roi.get("annual_savings_eur")
    payback = roi.get("payback_years")
    co2 = roi.get("co2_saved_kg")

    # Engagement summary
    eng_lines: list[str] = []
    if lead.get("outreach_sent_at"):
        eng_lines.append(f"- Email outreach sent: {lead['outreach_sent_at'][:10]}")
    if lead.get("outreach_opened_at"):
        eng_lines.append(f"- Email opened: {lead['outreach_opened_at'][:10]}")
    if lead.get("outreach_clicked_at"):
        eng_lines.append(f"- CTA clicked: {lead['outreach_clicked_at'][:10]}")
    if lead.get("dashboard_visited_at"):
        total_sec = lead.get("portal_total_time_sec") or 0
        scroll = lead.get("deepest_scroll_pct") or 0
        eng_lines.append(
            f"- Portal visited: {lead['dashboard_visited_at'][:10]} "
            f"(time: {total_sec}s, scroll: {scroll}%)"
        )
    if lead.get("whatsapp_initiated_at"):
        eng_lines.append(f"- WhatsApp CTA clicked: {lead['whatsapp_initiated_at'][:10]}")

    cmp_lines: list[str] = []
    for c in campaigns:
        line = f"  Step {c.get('sequence_step')}: {c.get('channel')} — {c.get('status')}"
        if c.get("email_subject"):
            line += f" (subject: {c['email_subject']})"
        if c.get("sent_at"):
            line += f" on {c['sent_at'][:10]}"
        cmp_lines.append(line)

    evt_lines = [
        f"  {e['event_type']} at {e['occurred_at'][:16]}"
        for e in events
    ]

    notes_section = ""
    if lead.get("feedback"):
        notes_section = f"\nOperator feedback: {lead['feedback']}"
        if lead.get("feedback_notes"):
            notes_section += f" — {lead['feedback_notes']}"

    context_block = f"""
Lead type: {subject_type.upper()}
{"Business: " + business if business else ""}
{"Contact person: " + person if person else ""}
{"Location: " + ", ".join(filter(None, [comune, provincia])) if comune or provincia else ""}

ROI data:
{"- Estimated power: " + str(kwp) + " kWp" if kwp else ""}
{"- Annual savings: €" + str(int(savings)) if savings else ""}
{"- Payback period: " + str(payback) + " years" if payback else ""}
{"- CO2 saved: " + str(int(co2)) + " kg/year" if co2 else ""}

Engagement history:
{chr(10).join(eng_lines) if eng_lines else "  (no engagement recorded yet)"}

Campaign sequence:
{chr(10).join(cmp_lines) if cmp_lines else "  (no campaigns yet)"}

Recent events:
{chr(10).join(evt_lines) if evt_lines else "  (no events)"}
{notes_section}
Installer: {tenant.get("business_name", "SolarLead")}
""".strip()

    system = (
        "You are a follow-up email assistant for an Italian solar energy company. "
        "Write concise, warm, and concrete follow-up emails IN ITALIAN. "
        "Always reference specific numbers from the ROI data to make the message personal. "
        "Tone: professional but friendly, never pushy. "
        "Call to action: book a free no-obligation site visit ('sopralluogo gratuito'). "
        "Length: 3-4 short paragraphs. "
        "Subject line: 8-12 words, specific to the lead's situation."
    )
    prompt = (
        f"Write a follow-up email for this lead:\n\n{context_block}\n\n"
        "Return a JSON object with two fields: "
        '"subject" (the email subject line in Italian) and '
        '"body" (the email body in plain text Italian, no HTML, no markdown).'
    )

    schema = '{"subject": "<string>", "body": "<string>"}'
    draft = await complete_json(prompt, schema_hint=schema, system=system, max_tokens=1200)

    subject_line = str(draft.get("subject") or "Follow-up — proposta solare personalizzata")
    body_text = str(draft.get("body") or "")

    if not body_text:
        raise HTTPException(
            status_code=502,
            detail="Claude did not return a draft body — please retry.",
        )

    return FollowUpDraftResponse(
        lead_id=lead_id,
        subject=subject_line,
        body=body_text,
    )


def _text_to_html(text: str) -> str:
    """Minimal plain-text → HTML converter for the send-draft endpoint.

    We intentionally avoid a heavy library: the only input is a
    Claude-generated body that is already structured with blank-line
    paragraph breaks and no markdown.
    """
    import html as _html

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "\n".join(
        f"<p>{_html.escape(p).replace(chr(10), '<br>')}</p>"
        for p in paragraphs
    )


@router.post("/{lead_id}/send-draft", response_model=SendDraftResponse)
async def send_draft(
    ctx: CurrentUser,
    lead_id: str,
    body: SendDraftRequest,
) -> SendDraftResponse:
    """Send the (user-edited) follow-up draft via Resend.

    Records a campaigns row (sequence_step = MAX+1 or 10 if fresh),
    updates outreach timestamps, and emits lead.outreach_sent. Bypasses
    OutreachAgent's template renderer so the operator's edited copy is
    sent verbatim.

    Tier-gated: requires advanced_analytics (Pro+).
    """
    from ..core.tier import Capability, require_capability
    from ..services.resend_service import SendEmailInput, send_email

    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # Auth-scope + tenant fetch
    lead_res = (
        sb.table("leads")
        .select(
            "id, tenant_id, pipeline_status, outreach_sent_at, outreach_channel, "
            "subjects(decision_maker_email, owner_first_name, business_name)"
        )
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    if not lead_res.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead = lead_res.data[0]

    tenant_res = (
        sb.table("tenants")
        .select("tier, settings, business_name, email_from_domain, email_from_name, contact_email")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    tenant = (tenant_res.data or [{}])[0]

    require_capability(tenant, Capability.ADVANCED_ANALYTICS)

    # Resolve recipient
    subj = lead.get("subjects") or {}
    if isinstance(subj, list):
        subj = subj[0] if subj else {}
    to_email = subj.get("decision_maker_email")
    if not to_email:
        raise HTTPException(
            status_code=422,
            detail="No verified email address on this lead.",
        )

    # Build from address (reuse OutreachAgent's helper)
    name = (tenant.get("email_from_name") or "").strip()
    domain = (tenant.get("email_from_domain") or "").strip()
    from_address = (
        f"{name or tenant.get('business_name', 'SolarLead')} "
        f"<outreach@{domain}>"
        if domain
        else f"{name or 'SolarLead'} <outreach@solarlead.it>"
    )

    html_body = _text_to_html(body.body)

    email_input = SendEmailInput(
        from_address=from_address,
        to=[to_email],
        subject=body.subject,
        html=html_body,
        text=body.body,
        reply_to=tenant.get("contact_email"),
        tags={"lead_id": lead_id, "type": "manual_followup"},
    )

    try:
        result = await send_email(email_input)
    except Exception as exc:
        log.warning("send_draft.resend_failed", lead_id=lead_id, err=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Sequence step: MAX(existing) + 1, floor 2 (manual follow-up is never step 1)
    steps_res = (
        sb.table("outreach_sends")
        .select("sequence_step")
        .eq("lead_id", lead_id)
        .order("sequence_step", desc=True)
        .limit(1)
        .execute()
    )
    max_step = (steps_res.data or [{"sequence_step": 1}])[0].get("sequence_step") or 1
    next_step = max(2, max_step + 1)

    now_iso = datetime.now(timezone.utc).isoformat()
    camp_res = (
        sb.table("outreach_sends")
        .insert(
            {
                "lead_id": lead_id,
                "tenant_id": tenant_id,
                "channel": "email",
                "sequence_step": next_step,
                "status": "sent",
                "template_id": "manual_followup",
                "email_subject": body.subject,
                "email_message_id": result.id,
                "scheduled_for": now_iso,
                "sent_at": now_iso,
                "cost_cents": 1,
            }
        )
        .select("id")
        .execute()
    )
    campaign_id = (camp_res.data or [{}])[0].get("id") or ""

    # Advance lead timestamps
    lead_update: dict[str, Any] = {}
    if not lead.get("outreach_sent_at"):
        lead_update["outreach_sent_at"] = now_iso
        lead_update["outreach_channel"] = "email"
    if lead_update:
        sb.table("leads").update(lead_update).eq("id", lead_id).execute()

    # Audit event
    try:
        sb.table("events").insert(
            {
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "event_type": "lead.outreach_sent",
                "event_source": "route.send_draft",
                "payload": {
                    "channel": "email",
                    "sequence_step": next_step,
                    "campaign_id": campaign_id,
                    "manual": True,
                },
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("send_draft.event_emit_failed", err=str(exc))

    # CRM webhook fanout
    await fire_crm_event(
        tenant_id=tenant_id,
        event_type="lead.outreach_sent",
        data={"lead_id": lead_id, "channel": "email", "campaign_id": campaign_id},
    )

    await audit_log(
        tenant_id,
        "lead.follow_up_sent",
        actor_user_id=ctx.sub,
        target_table="outreach_sends",
        target_id=campaign_id,
        diff={"subject": body.subject, "sequence_step": next_step},
    )
    log.info(
        "send_draft.sent",
        lead_id=lead_id,
        tenant_id=tenant_id,
        message_id=result.id,
        step=next_step,
    )
    return SendDraftResponse(
        ok=True,
        campaign_id=campaign_id,
        message_id=result.id,
    )


@router.post("/send-outreach-batch")
async def send_outreach_batch(
    ctx: CurrentUser,
    tier: Literal["hot", "warm", "cold", "rejected"] | None = Query(default=None),
    channel: Literal["email", "postal"] = Query(default="email"),
    only_new: bool = Query(
        default=True,
        description=(
            "When true (default), skip leads that already have "
            "outreach_sent_at populated. Set false to force a re-send."
        ),
    ),
    limit: int = Query(default=200, ge=1, le=2000),
) -> dict[str, object]:
    """Bulk-enqueue outreach for every matching lead.

    Typical use: on Tuesday morning the installer presses 'send this
    week's campaign' and we fan out one outreach job per Hot lead that
    hasn't been contacted yet.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    query = (
        sb.table("leads")
        .select("id, outreach_sent_at")
        .eq("tenant_id", tenant_id)
    )
    if tier:
        query = query.eq("score_tier", tier)
    if only_new:
        query = query.is_("outreach_sent_at", "null")
    res = query.order("score", desc=True).limit(limit).execute()

    queued = 0
    for lead in res.data or []:
        await enqueue(
            "outreach_task",
            {
                "tenant_id": tenant_id,
                "lead_id": lead["id"],
                "channel": channel,
                "force": not only_new,
            },
            job_id=f"outreach:{tenant_id}:{lead['id']}:{channel}",
        )
        queued += 1
    return {"ok": True, "queued": queued, "total_matching": len(res.data or [])}


@router.post("/score-pending-subjects")
async def score_pending_subjects(
    ctx: CurrentUser,
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, object]:
    """Promote every `subject` without a matching `leads` row by enqueueing
    a scoring_task. The Scoring agent INSERTs the lead on first run
    (see agents/scoring.py "Upsert lead row" block).

    This fills the gap between the hunter funnel (which creates
    `roofs` + `subjects`) and the outreach pipeline (which reads `leads`).
    The funnel itself doesn't auto-enqueue scoring today — this endpoint is
    the manual trigger the dashboard / ops calls after each scan.

    Idempotent per (tenant_id, roof_id, subject_id) via deterministic job_id;
    double-clicks collapse to a single worker run.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    # LEFT JOIN via two queries — PostgREST doesn't support anti-joins.
    # Pull all tenant subjects, then filter out those with a lead already.
    subj_res = (
        sb.table("subjects")
        .select("id, roof_id")
        .eq("tenant_id", tenant_id)
        .not_.is_("roof_id", "null")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    subjects = subj_res.data or []
    if not subjects:
        return {"ok": True, "queued": 0, "total_subjects": 0, "already_scored": 0}

    subject_ids = [s["id"] for s in subjects]
    existing_res = (
        sb.table("leads")
        .select("subject_id")
        .eq("tenant_id", tenant_id)
        .in_("subject_id", subject_ids)
        .execute()
    )
    already = {row["subject_id"] for row in (existing_res.data or [])}

    queued = 0
    for s in subjects:
        if s["id"] in already:
            continue
        await enqueue(
            "scoring_task",
            {
                "tenant_id": tenant_id,
                "roof_id": s["roof_id"],
                "subject_id": s["id"],
            },
            job_id=f"scoring:{tenant_id}:{s['roof_id']}:{s['id']}",
        )
        queued += 1
    return {
        "ok": True,
        "queued": queued,
        "total_subjects": len(subjects),
        "already_scored": len(already),
    }


@router.post("/rescore-all")
async def rescore_all(
    ctx: CurrentUser,
    tier: Literal["hot", "warm", "cold", "rejected"] | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, object]:
    """Bulk-enqueue rescoring for all leads (optionally filtered by tier).

    Returns the number of scoring jobs queued. Each job is idempotent via
    its deterministic job_id, so repeated calls are safe.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    query = (
        sb.table("leads")
        .select("id, roof_id, subject_id")
        .eq("tenant_id", tenant_id)
    )
    if tier:
        query = query.eq("score_tier", tier)
    res = query.limit(limit).execute()

    queued = 0
    for lead in res.data or []:
        await enqueue(
            "scoring_task",
            {
                "tenant_id": tenant_id,
                "roof_id": lead["roof_id"],
                "subject_id": lead["subject_id"],
            },
            job_id=f"scoring:{tenant_id}:{lead['roof_id']}:{lead['subject_id']}",
        )
        queued += 1
    return {"ok": True, "queued": queued, "total_matching": len(res.data or [])}
