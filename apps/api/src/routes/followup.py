"""Follow-up management endpoints.

Covers:
  POST /v1/followup/trigger  — operator-triggered immediate run of the
                               engagement follow-up evaluation for all
                               eligible leads in the tenant. Same logic
                               as the daily cron but fires right now.

  POST /v1/followup/bulk-draft — generate AI draft for multiple leads
                                 and queue sends in one shot.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..core.logging import get_logger

log = get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /trigger — manual trigger of the engagement follow-up cron
# ---------------------------------------------------------------------------

@router.post("/trigger")
async def trigger_followup_now(ctx: CurrentUser) -> dict[str, Any]:
    """Queue engagement-based follow-up evaluation for all eligible leads.

    Runs the same logic as the daily engagement_followup_cron (08:15 UTC)
    but immediately, for the calling tenant only. Useful when the operator
    wants to react to a batch of newly-engaged leads without waiting until
    the next morning.

    Idempotent: per-scenario cooldowns (set in followup_emails_sent) still
    apply — leads that received a follow-up today are skipped automatically.
    """
    from ..workers.cron import engagement_followup_for_tenant

    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # Verify tenant exists and is active
    tenant_res = (
        sb.table("tenants")
        .select("id, business_name")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    if not tenant_res.data:
        raise HTTPException(status_code=404, detail="Tenant not found")

    try:
        result = await engagement_followup_for_tenant(tenant_id)
        return {
            "ok": True,
            "queued": result.get("queued", 0),
            "skipped": result.get("skipped", 0),
            "message": (
                f"{result.get('queued', 0)} follow-up in coda, "
                f"{result.get('skipped', 0)} saltati (cooldown attivo o non idonei)."
            ),
        }
    except Exception as exc:
        log.warning("followup_trigger_failed", tenant_id=tenant_id, err=str(exc))
        raise HTTPException(status_code=500, detail=f"Errore durante il trigger: {exc}") from exc


# ---------------------------------------------------------------------------
# POST /bulk-draft — AI draft for multiple leads, queue sends
# ---------------------------------------------------------------------------

class BulkDraftRequest(BaseModel):
    lead_ids: list[str]
    send_immediately: bool = False  # if True, sends without operator review


class BulkDraftResult(BaseModel):
    lead_id: str
    ok: bool
    subject: str | None = None
    body: str | None = None
    error: str | None = None


@router.post("/bulk-draft", response_model=list[BulkDraftResult])
async def bulk_draft_followup(
    ctx: CurrentUser,
    req: BulkDraftRequest,
) -> list[BulkDraftResult]:
    """Generate AI follow-up drafts for multiple leads.

    - Generates up to 20 drafts concurrently (rate-limited to avoid
      hammering the Anthropic API).
    - If send_immediately=False (default): returns drafts for operator
      review. The operator uses POST /leads/{id}/send-draft per-lead.
    - If send_immediately=True: calls send-draft for each lead that
      gets a valid draft, using the tenant's follow-up sender address.

    Tier-gated: requires advanced_analytics (Pro+ / Founding).
    """
    from ..core.tier import Capability, require_capability
    from ..services.claude_service import complete_json

    if len(req.lead_ids) > 50:
        raise HTTPException(
            status_code=422,
            detail="Massimo 50 lead per richiesta bulk.",
        )

    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    tenant_res = (
        sb.table("tenants")
        .select(
            "id, tier, settings, business_name, email_from_domain, "
            "email_from_name, contact_email, followup_from_email"
        )
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    if not tenant_res.data:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant = tenant_res.data[0]

    require_capability(tenant, Capability.ADVANCED_ANALYTICS)

    # Fetch all requested leads in one query (tenant-scoped)
    leads_res = (
        sb.table("leads")
        .select(
            "id, pipeline_status, score, roi_data, engagement_score, "
            "outreach_sent_at, outreach_opened_at, outreach_clicked_at, "
            "dashboard_visited_at, portal_sessions, portal_total_time_sec, "
            "deepest_scroll_pct, whatsapp_initiated_at, feedback, feedback_notes, "
            "subjects(type, business_name, owner_first_name, owner_last_name, "
            "decision_maker_email), "
            "roofs(comune, provincia, estimated_kwp)"
        )
        .in_("id", req.lead_ids)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    leads_by_id = {row["id"]: row for row in (leads_res.data or [])}

    # Semaphore: max 5 concurrent Anthropic calls
    sem = asyncio.Semaphore(5)

    async def draft_one(lead_id: str) -> BulkDraftResult:
        if lead_id not in leads_by_id:
            return BulkDraftResult(lead_id=lead_id, ok=False, error="Lead non trovato o non autorizzato")

        lead = leads_by_id[lead_id]
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

        kwp = roi.get("estimated_kwp") or roof.get("estimated_kwp")
        savings = roi.get("annual_savings_eur")
        payback = roi.get("payback_years")

        context_block = f"""
Lead type: {(subj.get("type") or "unknown").upper()}
{"Business: " + business if business else ""}
{"Contact: " + person if person else ""}
{"Location: " + ", ".join(filter(None, [roof.get("comune"), roof.get("provincia")]))}
{"- Power: " + str(kwp) + " kWp" if kwp else ""}
{"- Savings: €" + str(int(savings)) + "/year" if savings else ""}
{"- Payback: " + str(payback) + " years" if payback else ""}
Engagement:
{chr(10).join(eng_lines) if eng_lines else "  (no engagement)"}
Installer: {tenant.get("business_name", "SolarLead")}
""".strip()

        system = (
            "You are a follow-up email assistant for an Italian solar energy company. "
            "Write concise, warm, concrete follow-up emails IN ITALIAN. "
            "Reference specific numbers from ROI data. "
            "Tone: professional but friendly, never pushy. "
            "CTA: book a free site visit ('sopralluogo gratuito'). "
            "Length: 3-4 short paragraphs. Subject: 8-12 words."
        )
        prompt = (
            f"Write a follow-up email for this lead:\n\n{context_block}\n\n"
            "Return JSON: {\"subject\": \"...\", \"body\": \"...\"} "
            "(plain text Italian, no HTML, no markdown)."
        )

        async with sem:
            try:
                draft = await complete_json(
                    prompt,
                    schema_hint='{"subject": "<string>", "body": "<string>"}',
                    system=system,
                    max_tokens=1000,
                )
            except Exception as exc:
                return BulkDraftResult(lead_id=lead_id, ok=False, error=str(exc))

        subject_line = str(draft.get("subject") or "")
        body_text = str(draft.get("body") or "")

        if not body_text:
            return BulkDraftResult(lead_id=lead_id, ok=False, error="Claude non ha restituito un corpo email")

        if req.send_immediately:
            # Call send logic directly (reuse helper)
            try:
                await _send_followup(
                    lead_id=lead_id,
                    lead=lead,
                    tenant=tenant,
                    subject=subject_line,
                    body=body_text,
                    sb=sb,
                )
            except Exception as exc:
                return BulkDraftResult(
                    lead_id=lead_id, ok=False,
                    subject=subject_line, body=body_text,
                    error=f"Invio fallito: {exc}",
                )

        return BulkDraftResult(
            lead_id=lead_id, ok=True,
            subject=subject_line,
            body=body_text,
        )

    results = await asyncio.gather(*[draft_one(lid) for lid in req.lead_ids])
    return list(results)


async def _send_followup(
    *,
    lead_id: str,
    lead: dict,
    tenant: dict,
    subject: str,
    body: str,
    sb: Any,
) -> None:
    """Internal helper: send a drafted follow-up email via Resend."""
    from datetime import datetime, timezone

    from ..services.resend_service import SendEmailInput, send_email

    subj = lead.get("subjects") or {}
    if isinstance(subj, list):
        subj = subj[0] if subj else {}
    to_email = subj.get("decision_maker_email")
    if not to_email:
        raise ValueError("No email address on this lead")

    followup_addr = (tenant.get("followup_from_email") or "").strip()
    if followup_addr and "<" not in followup_addr:
        sender_name = (
            tenant.get("email_from_name") or tenant.get("business_name") or "SolarLead"
        ).strip()
        from_address = f"{sender_name} <{followup_addr}>"
    elif followup_addr:
        from_address = followup_addr
    else:
        name = (tenant.get("email_from_name") or "").strip()
        domain = (tenant.get("email_from_domain") or "").strip()
        from_address = (
            f"{name or tenant.get('business_name', 'SolarLead')} <outreach@{domain}>"
            if domain
            else f"{name or 'SolarLead'} <outreach@solarlead.it>"
        )

    def _text_to_html(text: str) -> str:
        import html as html_lib
        paras = text.strip().split("\n\n")
        return "".join(f"<p>{html_lib.escape(p.strip()).replace(chr(10), '<br>')}</p>" for p in paras if p.strip())

    email_input = SendEmailInput(
        from_address=from_address,
        to=[to_email],
        subject=subject,
        html=_text_to_html(body),
        text=body,
        reply_to=tenant.get("contact_email"),
        tags={"lead_id": lead_id, "type": "bulk_manual_followup"},
    )
    await send_email(email_input)

    # Record in outreach_sends
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

    sb.table("outreach_sends").insert({
        "lead_id": lead_id,
        "tenant_id": tenant["id"],
        "channel": "email",
        "sequence_step": next_step,
        "status": "sent",
        "sent_at": now_iso,
        "email_subject": subject,
    }).execute()
