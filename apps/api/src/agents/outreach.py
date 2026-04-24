"""Outreach Agent — multi-channel first-contact and follow-up sends.

Supported channels
------------------
* **email**    — HTML email via Resend (B2B + B2C, sequence steps 1-3).
* **postal**   — Physical letter via Pixart (Sprint 8, currently skipped).
* **whatsapp** — WA text via 360dialog (reply-path only, steps ≥ 2).

Email pipeline (step 1):

    lead_id + tenant_id + channel=email
        ↓
    load lead + subject + roof + tenant (branding)
        ↓
    idempotency: if an email campaign already exists for this lead
        and outreach_sent_at is set → skip
        ↓
    compliance gate: if subject.pii_hash in global_blacklist → skip
        ↓
    tier gate: require EMAIL_OUTREACH / POSTAL_OUTREACH / WHATSAPP_OUTREACH
        ↓
    recipient resolution:
        channel=email     → subject.decision_maker_email (verified only)
        channel=whatsapp  → conversations.whatsapp_phone (lead must have
                            texted us first — no cold Meta templates)
        channel=postal    → abort with reason='postal_not_implemented'
        ↓
    optional: Claude writes a 1-sentence personalised opener (step 1 only)
        ↓
    render_outreach_email(ctx) → (subject, html, text) [email only]
    _build_wa_followup_text(…) → plain Italian text [whatsapp only]
        ↓
    send via Resend / 360dialog
        ↓
    INSERT campaigns (status='sent', provider message ID, cost_cents)
    UPDATE leads SET outreach_channel, outreach_sent_at,
        pipeline_status='sent'  [step 1 only]
        ↓
    emit lead.outreach_sent / lead.followup_sent_stepN event

Degradation:
  * Missing verified email → campaigns row status='failed', reason surfaced
    in dashboard. Worker does not crash.
  * Claude opener failure → opener skipped; template renders without it.
  * Resend 4xx → permanent failure recorded in campaigns row.
  * Resend 5xx → bubbles up; arq worker retries exponentially.
  * 360dialog failure → campaigns row status='failed', reason='dialog360_send_failed'.
  * No conversation row → WA skip with reason='no_whatsapp_conversation'.
"""

from __future__ import annotations

import random as _random
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..services.neverbounce_service import EmailVerification

from pydantic import BaseModel, Field

from ..core.config import settings
from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from ..core.tier import Capability, TierGateError, can_tenant_use, require_capability
from ..models.enums import CampaignStatus, LeadStatus, OutreachChannel, SubjectType
from ..services.claude_service import complete as claude_complete
from ..services.email_template_service import (
    OutreachContext,
    default_subject_for,
    render_outreach_email,
)
from ..services import dialog360_service
from ..services.dialog360_service import WA_COST_PER_MESSAGE_CENTS
from ..services.pixart_service import (
    LetterCampaignRequest,
    build_copy_overrides,
    resolve_template_id,
    submit_letter_campaign,
)
from ..services import inbox_service
from ..services.email_providers import get_provider
from ..services.email_providers.base import ProviderError
from ..services.inbox_service import (
    PAUSE_HOURS_429,
    PAUSE_HOURS_5XX,
    get_domain_purpose,
    get_tracking_host,
)
from ..services.rate_limit_service import acquire_email_quota
from ..services.resend_service import (
    RESEND_COST_PER_EMAIL_CENTS,
    ResendError,
    SendEmailInput,
)
from .base import AgentBase

log = get_logger(__name__)

DEFAULT_BRAND_PRIMARY = "#0F766E"
OUTREACH_TEMPLATE_VERSION = "v1"


class OutreachInput(BaseModel):
    tenant_id: str
    lead_id: str
    channel: OutreachChannel = OutreachChannel.EMAIL
    force: bool = Field(
        default=False,
        description=(
            "Re-send even when an outreach email has already gone out. "
            "Dashboard's 'resend' button sets this true. Only applies to "
            "the day-0 outreach (sequence_step=1). Follow-up steps dedupe "
            "on (lead_id, sequence_step) via campaigns."
        ),
    )
    sequence_step: int = Field(
        default=1,
        ge=1,
        le=4,
        description=(
            "Which step of the sequence we're sending. 1 = initial "
            "outreach (OutreachAgent default), 2/3 = follow-ups enqueued "
            "by the follow-up cron, 4 = breakup email at d+14 "
            "(conversational template only)."
        ),
    )


class OutreachOutput(BaseModel):
    lead_id: str
    campaign_id: str | None = None         # outreach_sends row id
    provider_id: str | None = None         # Resend message id
    status: str = CampaignStatus.PENDING.value
    cost_cents: int = 0
    skipped: bool = False
    reason: str | None = None


class OutreachAgent(AgentBase[OutreachInput, OutreachOutput]):
    name = "agent.outreach"

    async def execute(self, payload: OutreachInput) -> OutreachOutput:  # noqa: C901
        sb = get_service_client()

        # ------------------------------------------------------------------
        # 1) Load lead + subject + roof + tenant
        # ------------------------------------------------------------------
        lead = _load_single(sb, "leads", payload.lead_id, payload.tenant_id)
        if not lead:
            raise ValueError(f"lead {payload.lead_id} not found")

        subject = _load_single(sb, "subjects", lead["subject_id"], payload.tenant_id)
        roof = _load_single(sb, "roofs", lead["roof_id"], payload.tenant_id)
        if not subject or not roof:
            raise ValueError(
                f"lead {payload.lead_id} missing subject or roof rows"
            )

        tenant_res = (
            sb.table("tenants")
            .select(
                "id, business_name, brand_primary_color, brand_logo_url, "
                "contact_email, email_from_domain, email_from_name, "
                "email_from_domain_verified_at, tier, settings"
            )
            .eq("id", payload.tenant_id)
            .single()
            .execute()
        )
        tenant_row = tenant_res.data or {}

        # ------------------------------------------------------------------
        # 2) Idempotency guard
        #    * step 1: honour force=true (dashboard re-send). Default
        #      behaviour: skip if already sent.
        #    * step 2/3: always dedupe on (lead_id, sequence_step) in
        #      campaigns — force is ignored because the cron never wants
        #      to spam the same nudge twice.
        # ------------------------------------------------------------------
        if payload.sequence_step == 1:
            if lead.get("outreach_sent_at") and not payload.force:
                return OutreachOutput(
                    lead_id=payload.lead_id,
                    provider_id=None,
                    status=CampaignStatus.SENT.value,
                    skipped=True,
                    reason="already_sent",
                )
        else:
            existing = (
                sb.table("outreach_sends")
                .select("id, status")
                .eq("lead_id", payload.lead_id)
                .eq("sequence_step", payload.sequence_step)
                .eq("channel", OutreachChannel.EMAIL.value)
                .limit(1)
                .execute()
            )
            if existing.data:
                return OutreachOutput(
                    lead_id=payload.lead_id,
                    provider_id=None,
                    campaign_id=str(existing.data[0]["id"]),
                    status=str(existing.data[0].get("status") or ""),
                    skipped=True,
                    reason=f"step{payload.sequence_step}_already_sent",
                )

        # ------------------------------------------------------------------
        # 3) Compliance gate — blacklist check
        # ------------------------------------------------------------------
        pii_hash = subject.get("pii_hash")
        if pii_hash and _is_blacklisted(sb, pii_hash):
            return await self._record_skip(
                payload=payload,
                lead=lead,
                reason="blacklisted",
                pipeline_status=LeadStatus.BLACKLISTED.value,
            )

        # ------------------------------------------------------------------
        # 3b) Monthly outreach budget gate
        #     Read budget_outreach_eur_month from the economico module and
        #     compare against total campaigns.cost_cents for this calendar
        #     month.  Skip gracefully when budget is 0 or not configured
        #     (treat as "unlimited").
        # ------------------------------------------------------------------
        budget_eur = await _monthly_outreach_budget(sb, payload.tenant_id)
        if budget_eur and budget_eur > 0:
            month_spend_cents = await _monthly_campaign_spend_cents(sb, payload.tenant_id)
            if month_spend_cents >= int(budget_eur * 100):
                log.info(
                    "outreach.monthly_budget_exceeded",
                    lead_id=payload.lead_id,
                    tenant_id=payload.tenant_id,
                    month_spend_cents=month_spend_cents,
                    budget_eur=budget_eur,
                )
                return OutreachOutput(
                    lead_id=payload.lead_id,
                    skipped=True,
                    reason="monthly_budget_exceeded",
                )

        # ------------------------------------------------------------------
        # 4) Tier gate — founding plan only has email_outreach; postal /
        #    whatsapp are pro+. We check *before* the not-implemented
        #    block so a founding tenant gets the more actionable
        #    ``tier_lock_postal`` reason (the dashboard surfaces it as
        #    "piano insufficiente"), instead of the generic
        #    ``postal_not_implemented`` that a pro tenant would see.
        # ------------------------------------------------------------------
        channel_capability = _capability_for_channel(payload.channel)
        try:
            require_capability(tenant_row, channel_capability)
        except TierGateError as exc:
            log.info(
                "outreach.tier_locked",
                lead_id=payload.lead_id,
                tenant_id=payload.tenant_id,
                channel=payload.channel.value,
                current_tier=exc.current_tier,
                required_tier=exc.required_tier,
            )
            return await self._record_skip(
                payload=payload,
                lead=lead,
                reason=f"tier_lock_{payload.channel.value}",
                event_type="lead.outreach_skipped_tier",
                event_extra={
                    "channel": payload.channel.value,
                    "current_tier": exc.current_tier,
                    "required_tier": exc.required_tier,
                    "capability": channel_capability.value,
                },
            )

        # ------------------------------------------------------------------
        # 5) Channel routing
        # ------------------------------------------------------------------
        if payload.channel == OutreachChannel.POSTAL:
            return await self._execute_postal(
                payload=payload,
                lead=lead,
                subject=subject,
                tenant_row=tenant_row,
            )

        if payload.channel == OutreachChannel.WHATSAPP:
            return await self._execute_whatsapp(
                payload=payload,
                lead=lead,
                subject=subject,
                tenant_row=tenant_row,
            )

        # ------------------------------------------------------------------
        # 6) Recipient resolution (email path)
        # ------------------------------------------------------------------
        recipient = _resolve_recipient(subject)
        if not recipient:
            return await self._record_failure(
                payload=payload,
                lead=lead,
                tenant_row=tenant_row,
                subject=subject,
                failure_reason="no_verified_email",
            )

        # ------------------------------------------------------------------
        # 6b) NeverBounce pre-send validation (B.5)
        #     Only active when the API key is configured; degrades gracefully
        #     on NB service errors (log + proceed) so NB downtime never blocks
        #     legitimate sends. Step 1 only — follow-ups reuse the same address
        #     that was already validated on the first send.
        # ------------------------------------------------------------------
        if payload.sequence_step == 1 and settings.neverbounce_api_key:
            nb_result = await _check_neverbounce(
                email=recipient,
                tenant_id=payload.tenant_id,
                lead_id=payload.lead_id,
            )
            if nb_result is not None and not nb_result.sendable:
                log.info(
                    "outreach.neverbounce_rejected",
                    lead_id=payload.lead_id,
                    email=recipient,
                    result=nb_result.result.value,
                )
                return await self._record_failure(
                    payload=payload,
                    lead=lead,
                    tenant_row=tenant_row,
                    subject=subject,
                    failure_reason=f"neverbounce_{nb_result.result.value}",
                )

        # ------------------------------------------------------------------
        # 7) Build the outreach context (template inputs)
        # ------------------------------------------------------------------
        subject_type = subject.get("type") or SubjectType.UNKNOWN.value
        greeting = _greeting_for(subject, subject_type)
        # Use the per-domain tracking host for all public URLs when available.
        lead_url = _public_lead_url(lead.get("public_slug"), tracking_host=tracking_host)
        optout_url = _optout_url(lead.get("public_slug"), tracking_host=tracking_host)
        # Determine the email style for this send:
        # 1. Outreach domain (Gmail) → default plain_conversational
        # 2. Tenant setting in tenant_modules.outreach.email_style overrides
        # 3. Brand/Resend domain → default visual_preventivo
        inbox_email_style = (
            (selected_inbox or {}).get("email_style") or (
                "plain_conversational" if domain_purpose == "outreach"
                else "visual_preventivo"
            )
        )
        # Tenant-level override (from modules.outreach settings).
        t_settings: dict = dict(tenant_row.get("settings") or {})
        email_style = (
            t_settings.get("outreach_email_style")
            or inbox_email_style
        )

        # Sender first name: from inbox display_name (e.g. "Alfonso Gallo" → "Alfonso").
        sender_first_name: str | None = None
        if selected_inbox:
            dname = (selected_inbox.get("display_name") or "").strip()
            sender_first_name = dname.split()[0] if dname else None

        default_subject = default_subject_for(
            subject_type,
            tenant_row.get("business_name") or "SolarLead",
            sequence_step=payload.sequence_step,
            email_style=email_style,
            sender_first_name=sender_first_name,
        )

        # Opener is an expensive Claude call — keep it for step 1 only.
        # Follow-ups already have clear, hand-written copy; a synthetic
        # opener on day 11 reads robotic.
        personalized_opener = (
            await _maybe_generate_opener(
                subject=subject,
                subject_type=subject_type,
                tenant_row=tenant_row,
                tenant_id=payload.tenant_id,
                lead_id=payload.lead_id,
            )
            if payload.sequence_step == 1
            else None
        )

        # ------------------------------------------------------------------
        # 8a) A/B experiment: pick variant subject if an active experiment
        #     exists for this tenant (enterprise tier only, step 1 only).
        # ------------------------------------------------------------------
        experiment_id: str | None = None
        experiment_variant: str | None = None
        final_subject = default_subject

        if (
            payload.sequence_step == 1
            and can_tenant_use(tenant_row, Capability.AB_TESTING_TEMPLATES)
        ):
            from ..routes.experiments import load_active_experiment

            active_exp = load_active_experiment(payload.tenant_id)
            if active_exp:
                chosen = (
                    "a"
                    if _random.random() < (active_exp.get("split_pct", 50) / 100)
                    else "b"
                )
                subject_key = f"variant_{chosen}_subject"
                override = (active_exp.get(subject_key) or "").strip()
                if override:
                    final_subject = override
                    experiment_id = str(active_exp["id"])
                    experiment_variant = chosen
                    log.info(
                        "outreach.ab_variant_assigned",
                        lead_id=payload.lead_id,
                        experiment_id=experiment_id,
                        variant=chosen,
                    )

        # Read tenant's saved copy overrides (B.14)
        email_copy: dict = dict(t_settings.get("email_copy_overrides") or {})

        # Conversational templates skip the personalised opener (Claude call) —
        # the copy is already compact and the opener would bloat it.
        if email_style == "plain_conversational":
            personalized_opener = None

        ctx = OutreachContext(
            tenant_name=tenant_row.get("business_name") or "SolarLead",
            brand_primary_color=tenant_row.get("brand_primary_color")
            or DEFAULT_BRAND_PRIMARY,
            brand_logo_url=tenant_row.get("brand_logo_url"),
            greeting_name=greeting,
            lead_url=lead_url,
            optout_url=optout_url,
            subject_template=final_subject,
            subject_type=subject_type,
            roi=lead.get("roi_data") or None,
            hero_image_url=lead.get("rendering_image_url"),
            hero_gif_url=lead.get("rendering_gif_url"),
            personalized_opener=personalized_opener,
            business_name=subject.get("business_name"),
            ateco_code=subject.get("ateco_code"),
            ateco_description=subject.get("ateco_description"),
            sequence_step=payload.sequence_step,
            template_style=t_settings.get("email_style") or "classic",
            headline=email_copy.get("headline"),
            main_copy_1=email_copy.get("main_copy_1"),
            main_copy_2=email_copy.get("main_copy_2"),
            cta_text=email_copy.get("cta_text"),
            # Sprint 6.3 — conversational fields
            email_style=email_style,
            sender_first_name=sender_first_name,
            hq_province=lead.get("hq_province") or subject.get("hq_province"),
            ateco_desc=subject.get("ateco_description"),
            recipient_email=recipient,
            tenant_legal_name=tenant_row.get("legal_name"),
            tenant_vat_number=tenant_row.get("vat_number"),
            similar_province=lead.get("hq_province"),  # Step-3 case study hint
        )
        rendered = render_outreach_email(ctx)

        # ------------------------------------------------------------------
        # 8) Deliverability rate-limit — domain-level hourly cap
        #
        # Two caps work together:
        #   a) Domain-level (Redis): warm-up curve or hourly tier cap.
        #      Protects the *domain* reputation from burst sends.
        #   b) Inbox-level (Postgres, step 8b below): each inbox has its
        #      own daily_cap enforced by InboxSelector.pick_and_claim().
        #      Distributes volume across multiple sender addresses.
        #
        # On cap hit we *don't* create a campaigns row (unlike a send
        # failure) — the skip retries on the next window. The follow-up
        # cron re-evaluates candidates daily, so step-2/3 roll forward.
        # ------------------------------------------------------------------
        quota = await acquire_email_quota(tenant_row)
        if not quota.allowed:
            log.info(
                "outreach.rate_limited",
                lead_id=payload.lead_id,
                tenant_id=payload.tenant_id,
                domain=quota.domain,
                window=quota.window,
                used=quota.used,
                limit=quota.limit,
                verdict=quota.verdict,
            )
            return await self._record_skip(
                payload=payload,
                lead=lead,
                reason=f"rate_limited_{quota.window}",
                event_type="lead.outreach_ratelimited",
                event_extra={
                    "domain": quota.domain,
                    "window": quota.window,
                    "used": quota.used,
                    "limit": quota.limit,
                    "verdict": quota.verdict,
                },
            )

        # ------------------------------------------------------------------
        # 8b) Inbox selection — pick and claim a per-inbox send slot
        #
        # pick_and_claim() returns:
        #   - An inbox row → use its email as the From address.
        #   - None AND tenant has active inboxes → all at cap / paused
        #     → skip this send (will retry on next cron tick).
        #   - None AND tenant has NO inboxes → fall back to the legacy
        #     single-inbox address derived from tenant.email_from_domain.
        #
        # The campaign_inbox_ids filter is reserved for campaign-level
        # inbox restrictions (Phase A); today it is always None.
        # ------------------------------------------------------------------
        selected_inbox: dict[str, Any] | None = await inbox_service.pick_and_claim(
            sb,
            payload.tenant_id,
            campaign_inbox_ids=None,
        )

        # Detect "inboxes exist but all blocked" vs "no inboxes at all".
        has_multi_inbox = selected_inbox is not None or await _tenant_has_inboxes(
            sb, payload.tenant_id
        )

        if selected_inbox is None and has_multi_inbox:
            # Tenant has inboxes but none available right now.
            log.info(
                "outreach.inbox_cap_all_blocked",
                lead_id=payload.lead_id,
                tenant_id=payload.tenant_id,
            )
            return await self._record_skip(
                payload=payload,
                lead=lead,
                reason="inbox_daily_cap",
                event_type="lead.outreach_ratelimited",
                event_extra={"inbox_selector": "all_blocked"},
            )

        # Resolve From address: multi-inbox or legacy single-inbox.
        # Also resolve the tracking host and domain purpose for this send.
        tracking_host: str | None = None
        domain_purpose: str = "brand"

        if selected_inbox is not None:
            from_address = inbox_service.build_from_address(selected_inbox)
            reply_to = (
                selected_inbox.get("reply_to_email")
                or _build_reply_to(tenant_row, lead.get("public_slug"))
            )
            tracking_host = get_tracking_host(selected_inbox)
            domain_purpose = get_domain_purpose(selected_inbox)
        else:
            # Legacy path: outreach@{email_from_domain}
            from_address = _build_from_address(tenant_row)
            reply_to = _build_reply_to(tenant_row, lead.get("public_slug"))

        inbox_id: str | None = (
            str(selected_inbox["id"]) if selected_inbox else None
        )

        # ------------------------------------------------------------------
        # 9) Send via Resend
        # ------------------------------------------------------------------
        # ------------------------------------------------------------------
        # 8c) List-Unsubscribe headers (RFC 2369 + RFC 8058 one-click)
        #
        # Gmail & Yahoo require these for any "bulk" sender (>5k/day), and
        # penalise deliverability significantly when missing even for small
        # volumes. Two headers, both required for one-click:
        #   - List-Unsubscribe: <URL> (or <mailto:…>) → visible "Unsubscribe"
        #     link in the Gmail UI (next to "via agenda-pro.it").
        #   - List-Unsubscribe-Post: List-Unsubscribe=One-Click → tells Gmail
        #     that it may POST to the URL directly with that form body, no
        #     HTML confirmation page required. Our public optout endpoint
        #     (`POST /v1/public/lead/{slug}/optout`) accepts this shape.
        # ------------------------------------------------------------------
        extra_headers: dict[str, str] = {}
        slug = (lead.get("public_slug") or "").strip()
        if slug:
            # List-Unsubscribe one-click (RFC 8058).
            # When a custom tracking host is configured (Sprint 6.2), use
            # the optout URL on that domain so the header domain matches the
            # sending domain — Gmail rewards the alignment positively.
            # Otherwise fall back to the API's public optout endpoint.
            if tracking_host:
                one_click_url = f"https://{tracking_host.strip('/')}/optout/{slug}"
            else:
                api_base = (settings.api_base_url or "").rstrip("/")
                one_click_url = f"{api_base}/v1/public/lead/{slug}/optout"
            extra_headers["List-Unsubscribe"] = f"<{one_click_url}>"
            extra_headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

        send_input = SendEmailInput(
            from_address=from_address,
            to=[recipient],
            subject=rendered.subject,
            html=rendered.html,
            text=rendered.text,
            reply_to=reply_to,
            tags={
                "tenant_id": payload.tenant_id,
                "lead_id": payload.lead_id,
                "template": _template_id_for(subject_type),
            },
            headers=extra_headers or None,
        )

        # Dispatch via the provider registry. The inbox row carries
        # ``provider`` (from migration 0049) — Resend for legacy/brand,
        # ``gmail_oauth`` for per-inbox cold outreach via Google Workspace.
        # Legacy single-inbox path (no selected_inbox) falls through the
        # "resend" default.
        provider_name = (
            (selected_inbox or {}).get("provider") or "resend"
        )
        provider = get_provider(provider_name, sb=sb)
        provider_inbox_row: dict[str, Any] = selected_inbox or {"id": None}

        try:
            send_result = await provider.send(
                send_input, inbox=provider_inbox_row
            )
        except ProviderError as exc:
            log.warning(
                "outreach.provider_failed",
                lead_id=payload.lead_id,
                provider=provider_name,
                err=str(exc),
                kind=exc.kind,
                status_code=exc.status_code,
                inbox_id=inbox_id,
            )
            # Auto-pause the inbox on sender-side errors so other inboxes
            # can continue. Recipient-side permanent errors (bad address,
            # suppression hit) leave the inbox healthy.
            if inbox_id:
                if exc.kind == "rate_limited":
                    await inbox_service.pause_inbox(
                        sb, inbox_id,
                        hours=PAUSE_HOURS_429,
                        reason=f"{provider_name}_rate_limited",
                        tenant_id=payload.tenant_id,
                    )
                elif exc.kind == "server_error":
                    await inbox_service.pause_inbox(
                        sb, inbox_id,
                        hours=PAUSE_HOURS_5XX,
                        reason=f"{provider_name}_{exc.status_code}",
                        tenant_id=payload.tenant_id,
                    )
                # auth_failed is terminal — the GmailProvider already
                # flipped ``active=false`` and recorded the error on the
                # inbox row. Don't pile an extra pause on top.
            return await self._record_failure(
                payload=payload,
                lead=lead,
                tenant_row=tenant_row,
                subject=subject,
                failure_reason=f"{provider_name}_{exc.kind}: {str(exc)[:200]}",
            )
        except ResendError as exc:  # defensive: legacy callsites may still raise
            log.warning(
                "outreach.resend_failed",
                lead_id=payload.lead_id,
                err=str(exc),
                status_code=exc.status_code,
                inbox_id=inbox_id,
            )
            return await self._record_failure(
                payload=payload,
                lead=lead,
                tenant_row=tenant_row,
                subject=subject,
                failure_reason=f"resend_error: {str(exc)[:200]}",
            )

        # ------------------------------------------------------------------
        # 10) Persist campaign + advance lead pipeline
        # ------------------------------------------------------------------
        now_iso = datetime.now(timezone.utc).isoformat()
        campaign_insert: dict[str, Any] = {
            "tenant_id": payload.tenant_id,
            "lead_id": payload.lead_id,
            "channel": OutreachChannel.EMAIL.value,
            "template_id": _template_id_for(
                subject_type, sequence_step=payload.sequence_step
            ),
            "sequence_step": payload.sequence_step,
            "email_message_id": send_result.message_id,
            "email_subject": rendered.subject,
            "scheduled_for": now_iso,
            "sent_at": now_iso,
            "cost_cents": RESEND_COST_PER_EMAIL_CENTS,
            "status": CampaignStatus.SENT.value,
        }
        # Attribute the send to the inbox that was used (for per-inbox
        # deliverability analytics). Null when using legacy single-inbox path.
        if inbox_id:
            campaign_insert["inbox_id"] = inbox_id
        if experiment_id and experiment_variant:
            campaign_insert["experiment_id"] = experiment_id
            campaign_insert["experiment_variant"] = experiment_variant
        campaign_res = (
            sb.table("outreach_sends").insert(campaign_insert).execute()
        )
        campaign_id = (
            (campaign_res.data[0]["id"])
            if campaign_res.data
            else None
        )

        # Only the day-0 outreach moves the pipeline to ``sent``. Follow-
        # up steps preserve the current status so webhook events
        # (delivered/opened/clicked) continue to advance the lead
        # monotonically — TrackingAgent's projector handles that.
        if payload.sequence_step == 1:
            sb.table("leads").update(
                {
                    "outreach_channel": OutreachChannel.EMAIL.value,
                    "outreach_sent_at": now_iso,
                    "pipeline_status": LeadStatus.SENT.value,
                }
            ).eq("id", payload.lead_id).execute()

        _log_api_cost(
            sb,
            tenant_id=payload.tenant_id,
            endpoint="emails:send",
            cost_cents=RESEND_COST_PER_EMAIL_CENTS,
            status="success",
            metadata={
                "lead_id": payload.lead_id,
                "message_id": send_result.message_id,
            },
        )

        out = OutreachOutput(
            lead_id=payload.lead_id,
            campaign_id=campaign_id,
            provider_id=send_result.message_id,
            status=CampaignStatus.SENT.value,
            cost_cents=RESEND_COST_PER_EMAIL_CENTS,
        )
        event_type = (
            "lead.outreach_sent"
            if payload.sequence_step == 1
            else f"lead.followup_sent_step{payload.sequence_step}"
        )
        await self._emit_event(
            event_type=event_type,
            payload=out.model_dump()
            | {
                "channel": OutreachChannel.EMAIL.value,
                "sequence_step": payload.sequence_step,
                "template_id": _template_id_for(
                    subject_type, sequence_step=payload.sequence_step
                ),
                "subject": rendered.subject,
                "recipient_domain": recipient.split("@", 1)[1]
                if "@" in recipient
                else "",
            },
            tenant_id=payload.tenant_id,
            lead_id=payload.lead_id,
        )

        # Emit lead.contacted as the unified "first touch" marker.
        # Distinct from lead.outreach_sent (which is per-send): this event
        # fires exactly once per lead — when the prospect is contacted for
        # the first time. Reporting uses it to split scan_candidates (never
        # contacted) from leads in the active pipeline.
        if payload.sequence_step == 1:
            await self._emit_event(
                event_type="lead.contacted",
                payload={
                    "lead_id": payload.lead_id,
                    "channel": OutreachChannel.EMAIL.value,
                    "campaign_id": campaign_id,
                    "recipient_domain": recipient.split("@", 1)[1]
                    if "@" in recipient
                    else "",
                },
                tenant_id=payload.tenant_id,
                lead_id=payload.lead_id,
            )

        return out

    # ------------------------------------------------------------------
    # Failure / skip recorders
    # ------------------------------------------------------------------

    async def _record_skip(
        self,
        *,
        payload: OutreachInput,
        lead: dict[str, Any],
        reason: str,
        pipeline_status: str | None = None,
        event_type: str = "lead.outreach_skipped",
        event_extra: dict[str, Any] | None = None,
    ) -> OutreachOutput:
        sb = get_service_client()
        if pipeline_status:
            sb.table("leads").update(
                {"pipeline_status": pipeline_status}
            ).eq("id", payload.lead_id).execute()
        event_payload: dict[str, Any] = {
            "lead_id": payload.lead_id,
            "reason": reason,
        }
        if event_extra:
            event_payload.update(event_extra)
        await self._emit_event(
            event_type=event_type,
            payload=event_payload,
            tenant_id=payload.tenant_id,
            lead_id=payload.lead_id,
        )
        return OutreachOutput(
            lead_id=payload.lead_id,
            status=CampaignStatus.CANCELLED.value,
            skipped=True,
            reason=reason,
        )

    async def _record_failure(
        self,
        *,
        payload: OutreachInput,
        lead: dict[str, Any],
        tenant_row: dict[str, Any],
        subject: dict[str, Any],
        failure_reason: str,
    ) -> OutreachOutput:
        """Insert a campaigns row with status=failed for dashboard visibility."""
        sb = get_service_client()
        now_iso = datetime.now(timezone.utc).isoformat()
        subject_type = subject.get("type") or SubjectType.UNKNOWN.value
        failure_insert = {
            "tenant_id": payload.tenant_id,
            "lead_id": payload.lead_id,
            "channel": payload.channel.value,
            "template_id": _template_id_for(
                subject_type, sequence_step=payload.sequence_step
            ),
            "sequence_step": payload.sequence_step,
            "scheduled_for": now_iso,
            "cost_cents": 0,
            "status": CampaignStatus.FAILED.value,
            "failure_reason": failure_reason,
        }
        res = sb.table("outreach_sends").insert(failure_insert).execute()
        campaign_id = res.data[0]["id"] if res.data else None

        await self._emit_event(
            event_type="lead.outreach_failed",
            payload={
                "lead_id": payload.lead_id,
                "failure_reason": failure_reason,
                "campaign_id": campaign_id,
            },
            tenant_id=payload.tenant_id,
            lead_id=payload.lead_id,
        )
        return OutreachOutput(
            lead_id=payload.lead_id,
            campaign_id=campaign_id,
            status=CampaignStatus.FAILED.value,
            skipped=True,
            reason=failure_reason,
        )

    async def _execute_postal(
        self,
        *,
        payload: "OutreachInput",
        lead: dict[str, Any],
        subject: dict[str, Any],
        tenant_row: dict[str, Any],
    ) -> "OutreachOutput":
        """Postal path — B2C residential letter via Pixart.

        Pixart's product model is per-CAP distribution: we submit the
        lead's postal code and Pixart distributes via Poste Italiane
        across all addresses in that zone. This fits the B2C discovery
        pattern (we've identified the CAP as high-value, not a specific
        door number).

        In development without ``PIXART_API_KEY`` the service runs in
        stub mode — it generates a local job ID and logs the payload, so
        the campaign row is still persisted and the Tracking Agent can
        process future webhook events normally.

        Only B2C subjects are eligible for postal outreach (B2B gets
        email). The CAP is required — if the subject has no postal_cap
        we skip and surface the reason so the dashboard can flag the
        data gap.
        """
        # Postal only makes sense for B2C residential subjects
        subject_type = subject.get("type") or SubjectType.UNKNOWN.value
        if subject_type != SubjectType.B2C.value:
            return await self._record_skip(
                payload=payload,
                lead=lead,
                reason="postal_b2b_not_supported",
            )

        postal_cap = (subject.get("postal_cap") or "").strip()
        if not postal_cap:
            return await self._record_skip(
                payload=payload,
                lead=lead,
                reason="postal_no_cap",
            )

        t_settings: dict = dict(tenant_row.get("settings") or {})
        email_copy: dict = dict(t_settings.get("email_copy_overrides") or {})

        request = LetterCampaignRequest(
            tenant_id=payload.tenant_id,
            audience_id=payload.lead_id,
            template_id=resolve_template_id(
                payload.tenant_id,
                bucket=_income_bucket_for(subject),
            ),
            caps=[postal_cap],
            tenant_brand_name=tenant_row.get("business_name"),
            copy_overrides=build_copy_overrides(
                tenant_brand_name=tenant_row.get("business_name"),
                cta_primary=email_copy.get("cta_text"),
            ),
        )

        try:
            result = await submit_letter_campaign(request)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "outreach.postal_submit_failed",
                lead_id=payload.lead_id,
                err=str(exc),
            )
            return await self._record_failure(
                payload=payload,
                lead=lead,
                tenant_row=tenant_row,
                subject=subject,
                failure_reason=f"pixart_submit_error: {str(exc)[:200]}",
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        campaign_insert: dict[str, Any] = {
            "tenant_id": payload.tenant_id,
            "lead_id": payload.lead_id,
            "channel": OutreachChannel.POSTAL.value,
            "template_id": request.template_id,
            "sequence_step": payload.sequence_step,
            "postal_provider_order_id": result.pixart_job_id,
            "scheduled_for": now_iso,
            "sent_at": now_iso,
            "cost_cents": 0,   # Pixart invoices monthly; updated on webhook
            "status": CampaignStatus.SENT.value,
        }
        if result.stub:
            campaign_insert["failure_reason"] = "pixart_stub_mode"
        sb = get_service_client()
        campaign_res = sb.table("outreach_sends").insert(campaign_insert).execute()
        campaign_id = campaign_res.data[0]["id"] if campaign_res.data else None

        if payload.sequence_step == 1:
            sb.table("leads").update(
                {
                    "outreach_channel": OutreachChannel.POSTAL.value,
                    "outreach_sent_at": now_iso,
                    "pipeline_status": LeadStatus.SENT.value,
                }
            ).eq("id", payload.lead_id).execute()

        out = OutreachOutput(
            lead_id=payload.lead_id,
            campaign_id=campaign_id,
            provider_id=result.pixart_job_id,
            status=CampaignStatus.SENT.value,
        )
        postal_event_type = (
            "lead.outreach_sent"
            if payload.sequence_step == 1
            else f"lead.followup_sent_step{payload.sequence_step}"
        )
        await self._emit_event(
            event_type=postal_event_type,
            payload=out.model_dump()
            | {
                "channel": OutreachChannel.POSTAL.value,
                "sequence_step": payload.sequence_step,
                "pixart_job_id": result.pixart_job_id,
                "caps_submitted": result.caps_submitted,
                "stub": result.stub,
            },
            tenant_id=payload.tenant_id,
            lead_id=payload.lead_id,
        )
        if payload.sequence_step == 1:
            await self._emit_event(
                event_type="lead.contacted",
                payload={
                    "lead_id": payload.lead_id,
                    "channel": OutreachChannel.POSTAL.value,
                    "campaign_id": campaign_id,
                    "pixart_job_id": result.pixart_job_id,
                },
                tenant_id=payload.tenant_id,
                lead_id=payload.lead_id,
            )
        log.info(
            "outreach.postal_submitted",
            lead_id=payload.lead_id,
            tenant_id=payload.tenant_id,
            job_id=result.pixart_job_id,
            cap=postal_cap,
            stub=result.stub,
        )
        return out

    async def _execute_whatsapp(
        self,
        *,
        payload: "OutreachInput",
        lead: dict[str, Any],
        subject: dict[str, Any],
        tenant_row: dict[str, Any],
    ) -> "OutreachOutput":
        """WA follow-up path (sequence_step ≥ 2, reply-path only).

        We deliberately block step 1 (cold outbound) because Meta's
        Business Policy requires pre-approved Message Templates for
        marketing messages to users who haven't messaged us first.
        Steps 2+ are safe: the lead already replied on WhatsApp
        (conversation row exists), putting us inside the 24-hour
        service-conversation window where free-form text is allowed.

        Phone resolution: we look up ``conversations.whatsapp_phone``
        by ``lead_id`` — if no conversation row exists the lead has
        never texted us, so we skip rather than attempt cold outreach.
        """
        # Cold outbound guard
        if payload.sequence_step == 1:
            return await self._record_skip(
                payload=payload,
                lead=lead,
                reason="wa_cold_outbound_blocked",
            )

        sb = get_service_client()

        # Resolve phone from existing conversation row
        conv_res = (
            sb.table("conversations")
            .select("whatsapp_phone, state")
            .eq("lead_id", payload.lead_id)
            .limit(1)
            .execute()
        )
        if not conv_res.data:
            return await self._record_skip(
                payload=payload,
                lead=lead,
                reason="no_whatsapp_conversation",
            )

        wa_phone: str = conv_res.data[0]["whatsapp_phone"]
        conv_state: str = conv_res.data[0].get("state", "active")

        # Don't send to conversations already handed off or closed —
        # operator is managing them; injecting an automated message
        # would be confusing.
        if conv_state != "active":
            return await self._record_skip(
                payload=payload,
                lead=lead,
                reason=f"wa_conversation_{conv_state}",
            )

        # Build short follow-up copy (Italian, plain text)
        subject_type = subject.get("type") or SubjectType.UNKNOWN.value
        greeting = _greeting_for(subject, subject_type)
        lead_url = _public_lead_url(lead.get("public_slug"), tracking_host=None)  # WA channel: no custom tracking host
        wa_text = _build_wa_followup_text(
            greeting=greeting,
            step=payload.sequence_step,
            tenant_name=tenant_row.get("business_name") or "SolarLead",
            lead_url=lead_url,
        )

        wamid = await dialog360_service.send_wa_message(
            phone=wa_phone,
            text=wa_text,
            tenant_id=payload.tenant_id,
        )
        if not wamid:
            return await self._record_failure(
                payload=payload,
                lead=lead,
                tenant_row=tenant_row,
                subject=subject,
                failure_reason="dialog360_send_failed",
            )

        # Persist campaign row — reuse email_message_id for the wamid
        now_iso = datetime.now(timezone.utc).isoformat()
        campaign_insert: dict[str, Any] = {
            "tenant_id": payload.tenant_id,
            "lead_id": payload.lead_id,
            "channel": OutreachChannel.WHATSAPP.value,
            "template_id": f"wa_followup_step{payload.sequence_step}",
            "sequence_step": payload.sequence_step,
            "email_message_id": wamid,   # provider message ID
            "scheduled_for": now_iso,
            "sent_at": now_iso,
            "cost_cents": WA_COST_PER_MESSAGE_CENTS,
            "status": CampaignStatus.SENT.value,
        }
        campaign_res = sb.table("outreach_sends").insert(campaign_insert).execute()
        campaign_id = campaign_res.data[0]["id"] if campaign_res.data else None

        _log_api_cost(
            sb,
            tenant_id=payload.tenant_id,
            endpoint="whatsapp:send",
            cost_cents=WA_COST_PER_MESSAGE_CENTS,
            status="success",
            metadata={"lead_id": payload.lead_id, "wamid": wamid},
        )

        event_type = f"lead.followup_sent_step{payload.sequence_step}"
        out = OutreachOutput(
            lead_id=payload.lead_id,
            campaign_id=campaign_id,
            provider_id=wamid,
            status=CampaignStatus.SENT.value,
        )
        await self._emit_event(
            event_type=event_type,
            payload=out.model_dump()
            | {
                "channel": OutreachChannel.WHATSAPP.value,
                "sequence_step": payload.sequence_step,
                "template_id": f"wa_followup_step{payload.sequence_step}",
                "wa_phone_suffix": wa_phone[-4:] if len(wa_phone) >= 4 else "????",
            },
            tenant_id=payload.tenant_id,
            lead_id=payload.lead_id,
        )
        log.info(
            "outreach.wa_sent",
            lead_id=payload.lead_id,
            tenant_id=payload.tenant_id,
            step=payload.sequence_step,
            wamid=wamid,
        )
        return out


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable)
# ---------------------------------------------------------------------------


def _capability_for_channel(channel: OutreachChannel) -> Capability:
    """Map an outreach channel to the tier Capability that gates it.

    Keep this in sync with ``CAPABILITIES`` in ``src/core/tier.py`` —
    the dashboard surfaces the same mapping through its plan matrix.
    """
    if channel == OutreachChannel.POSTAL:
        return Capability.POSTAL_OUTREACH
    if channel == OutreachChannel.WHATSAPP:
        return Capability.WHATSAPP_OUTREACH
    # Default = email, which is allowed on every tier today but we
    # still run it through the gate for parity / future-proofing (e.g.
    # if we ever decide to rate-limit email on founding).
    return Capability.EMAIL_OUTREACH


def _resolve_recipient(subject: dict[str, Any]) -> str | None:
    """Return the first eligible recipient email for this subject.

    For B2B: ``decision_maker_email`` with ``decision_maker_email_verified``.
    For B2C: not supported yet (Sprint 8 will go through postal anyway).
    """
    if subject.get("type") != SubjectType.B2B.value:
        return None
    email = subject.get("decision_maker_email")
    verified = bool(subject.get("decision_maker_email_verified"))
    if not email or not verified:
        return None
    return str(email).strip().lower() or None


def _greeting_for(subject: dict[str, Any], subject_type: str) -> str:
    """Compose a greeting-appropriate salutation."""
    if subject_type == SubjectType.B2B.value:
        dm_name = (subject.get("decision_maker_name") or "").strip()
        if dm_name:
            return dm_name
        biz = (subject.get("business_name") or "").strip()
        if biz:
            return biz
        return "Gentili responsabili"
    if subject_type == SubjectType.B2C.value:
        first = (subject.get("owner_first_name") or "").strip()
        last = (subject.get("owner_last_name") or "").strip()
        full = " ".join(p for p in (first, last) if p)
        return full or "Gentile proprietario"
    return "Buongiorno"


def _template_id_for(subject_type: str, *, sequence_step: int = 1) -> str:
    st = (subject_type or "").lower()
    if st == SubjectType.B2B.value:
        stem = f"outreach_b2b_{OUTREACH_TEMPLATE_VERSION}"
    elif st == SubjectType.B2C.value:
        stem = f"outreach_b2c_{OUTREACH_TEMPLATE_VERSION}"
    else:
        stem = f"outreach_generic_{OUTREACH_TEMPLATE_VERSION}"
    if sequence_step and sequence_step != 1:
        stem = f"{stem}_step{sequence_step}"
    return stem


def _build_reply_to(tenant_row: dict[str, Any], public_slug: str | None) -> str | None:
    """Build a slug-encoded Reply-To address so inbound replies can be matched
    back to the originating lead without fragile In-Reply-To header lookups.

    Format: ``reply+{slug}@{email_from_domain}``
    Fallback (no domain configured): ``reply+{slug}@solarlead.it``
    If ``public_slug`` is absent, fall back to the tenant contact_email so
    replies still reach the operator.
    """
    slug = (public_slug or "").strip()
    if not slug:
        return tenant_row.get("contact_email") or None

    domain = (tenant_row.get("email_from_domain") or "").strip() or "solarlead.it"
    return f"reply+{slug}@{domain}"


def _build_from_address(tenant_row: dict[str, Any]) -> str:
    """Build the RFC 5322 From header from tenant branding.

    Falls back to the platform sender when the tenant hasn't wired their
    domain yet — we still look sensible (``SolarLead <noreply@solarlead.it>``).
    """
    name = (tenant_row.get("email_from_name") or "").strip()
    domain = (tenant_row.get("email_from_domain") or "").strip()
    if domain:
        local = "outreach"
        address = f"{local}@{domain}"
    else:
        address = "outreach@solarlead.it"
    display = name or tenant_row.get("business_name") or "SolarLead"
    return f"{display} <{address}>"


def _public_lead_url(
    public_slug: str | None,
    *,
    tracking_host: str | None = None,
) -> str:
    """Build the public lead portal URL.

    When ``tracking_host`` is set (custom per-domain CNAME, Sprint 6.2),
    the link uses that host so click-tracking stays on the sender's own
    domain — better deliverability + brand alignment.

    Example:
        ``"https://go.agendasolar.it/l/abc123"`` instead of
        ``"https://portal.solarld.app/l/abc123"``
    """
    from ..core.config import settings

    if tracking_host:
        base = f"https://{tracking_host.strip('/')}"
    else:
        base = (settings.next_public_lead_portal_url or "").rstrip("/")
    slug = (public_slug or "").strip()
    return f"{base}/l/{slug}" if slug else base


def _optout_url(
    public_slug: str | None,
    *,
    tracking_host: str | None = None,
) -> str:
    from ..core.config import settings

    if tracking_host:
        base = f"https://{tracking_host.strip('/')}"
    else:
        base = (settings.next_public_lead_portal_url or "").rstrip("/")
    slug = (public_slug or "").strip()
    return f"{base}/optout/{slug}" if slug else f"{base}/optout"


# ---------------------------------------------------------------------------
# Side-effectful helpers
# ---------------------------------------------------------------------------


def _load_single(
    sb: Any, table: str, row_id: str, tenant_id: str
) -> dict[str, Any] | None:
    res = (
        sb.table(table)
        .select("*")
        .eq("id", row_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    data = res.data or []
    return data[0] if data else None


def _is_blacklisted(sb: Any, pii_hash: str) -> bool:
    """Check the cross-tenant global_blacklist table."""
    try:
        res = (
            sb.table("global_blacklist")
            .select("pii_hash")
            .eq("pii_hash", pii_hash)
            .limit(1)
            .execute()
        )
        return bool(res.data)
    except Exception as exc:  # noqa: BLE001
        log.warning("outreach.blacklist_check_failed", err=str(exc))
        # Fail closed: if we can't verify, skip rather than spam.
        return True


async def _maybe_generate_opener(
    *,
    subject: dict[str, Any],
    subject_type: str,
    tenant_row: dict[str, Any],
    tenant_id: str | None = None,
    lead_id: str | None = None,
) -> str | None:
    """Ask Claude for a one-sentence personalised opener.

    The Claude service already retries 3× with exponential backoff
    (see ``claude_service.complete``). If all retries fail we fall back
    to the generic template (``personalized_opener`` is optional in the
    Jinja email). **However** we no longer fail silently — we write an
    ``api_usage_log`` entry with ``status='error'`` so the dashboard
    analytics surface the degradation. Without this, a slow Claude
    service could quietly drain personalisation for days unnoticed.
    """
    from ..core.config import settings as _s

    if not _s.anthropic_api_key:
        return None

    tenant_name = tenant_row.get("business_name") or "SolarLead"

    if subject_type == SubjectType.B2B.value:
        biz = subject.get("business_name") or "l'azienda"
        ateco_desc = subject.get("ateco_description") or ""
        ateco_frag = f" (settore: {ateco_desc})" if ateco_desc else ""
        prompt = (
            f"Scrivi UNA singola frase in italiano, massimo 25 parole, "
            f"tono professionale ma caldo, che apra una email commerciale "
            f"proveniente da {tenant_name}. Destinatario: {biz}{ateco_frag}. "
            f"Non salutare (il saluto è già presente), non firmare, "
            f"non menzionare pannelli solari direttamente — accennare solo "
            f"al risparmio energetico come leva. Nessuna emoji, nessun "
            f"asterisco, solo prosa. Restituisci SOLO la frase."
        )
    elif subject_type == SubjectType.B2C.value:
        city = subject.get("postal_city") or ""
        city_frag = f" a {city}" if city else ""
        prompt = (
            f"Scrivi UNA singola frase in italiano, massimo 25 parole, "
            f"tono cordiale e confidenziale, che apra una email di "
            f"{tenant_name} verso un proprietario di casa{city_frag}. "
            f"Non salutare (già fatto), non firmare, non menzionare "
            f"esplicitamente pannelli solari — parlare di bolletta e "
            f"autonomia energetica. Nessuna emoji. Solo prosa. "
            f"Restituisci SOLO la frase."
        )
    else:
        return None

    try:
        text = await claude_complete(
            prompt,
            system="Sei un copywriter commerciale italiano per il settore energia.",
            max_tokens=120,
            temperature=0.7,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "outreach.opener_claude_failed",
            tenant_id=tenant_id,
            lead_id=lead_id,
            subject_type=subject_type,
            err_type=type(exc).__name__,
            err=str(exc),
        )
        # Surface the failure in api_usage_log so analytics catch drift.
        if tenant_id:
            sb = get_service_client()
            try:
                sb.table("api_usage_log").insert(
                    {
                        "tenant_id": tenant_id,
                        "provider": "anthropic",
                        "endpoint": "messages:create",
                        "request_count": 1,
                        "cost_cents": 0,
                        "status": "error",
                        "metadata": {
                            "purpose": "outreach_opener",
                            "lead_id": lead_id,
                            "subject_type": subject_type,
                            "err_type": type(exc).__name__,
                        },
                    }
                ).execute()
            except Exception as log_exc:  # noqa: BLE001
                log.warning(
                    "outreach.opener_usage_log_failed",
                    err=str(log_exc),
                )
        return None

    cleaned = (text or "").strip().strip('"').strip("'")
    if not cleaned:
        log.info(
            "outreach.opener_empty_response",
            tenant_id=tenant_id,
            lead_id=lead_id,
        )
        return None
    # Keep the opener to one sentence — trim at first hard break.
    for sep in ("\n", "\r"):
        if sep in cleaned:
            cleaned = cleaned.split(sep, 1)[0].strip()
    return cleaned or None


async def _check_neverbounce(
    *,
    email: str,
    tenant_id: str,
    lead_id: str,
) -> "EmailVerification | None":
    """Run NeverBounce single-email check; return None on any error.

    Errors are swallowed so NeverBounce downtime never blocks legitimate
    sends. The result is logged to api_usage_log for reputation analytics.
    """
    from ..services.neverbounce_service import (
        EmailVerification,
        NeverBounceError,
        NEVERBOUNCE_COST_PER_CALL_CENTS,
        verify_email,
    )

    try:
        result = await verify_email(email)
        # Log cost regardless of verdict
        sb = get_service_client()
        try:
            sb.table("api_usage_log").insert({
                "tenant_id": tenant_id,
                "provider": "neverbounce",
                "endpoint": "single/check",
                "request_count": 1,
                "cost_cents": NEVERBOUNCE_COST_PER_CALL_CENTS,
                "status": "success",
                "metadata": {
                    "email_domain": email.split("@", 1)[1] if "@" in email else "",
                    "result": result.result.value,
                    "lead_id": lead_id,
                },
            }).execute()
        except Exception:  # noqa: BLE001
            pass
        return result
    except NeverBounceError as exc:
        log.warning(
            "outreach.neverbounce_api_error",
            lead_id=lead_id,
            err=str(exc),
        )
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "outreach.neverbounce_unexpected_error",
            lead_id=lead_id,
            err_type=type(exc).__name__,
            err=str(exc),
        )
        return None


def _income_bucket_for(subject: dict[str, Any]) -> str:
    """Map a B2C subject to one of Pixart's template buckets.

    Pixart templates are provisioned per-bucket so the letter copy can
    be tuned per income segment. Until the template registry is wired
    (``tenants.settings.pixart_templates``) we derive a deterministic
    bucket name from the roof's annual solar potential as a rough proxy
    for property value.

    Buckets: ``high`` (≥8 kWp proxy), ``standard`` (default).
    """
    # The ROI data may carry an estimated kWp derived from roof area
    # and irradiance — use it as an income proxy.
    roi = subject.get("roi_data") or {}
    if isinstance(roi, dict):
        kwp = float(roi.get("estimated_kwp") or 0)
        if kwp >= 8:
            return "high"
    return "standard"


def _build_wa_followup_text(
    *,
    greeting: str,
    step: int,
    tenant_name: str,
    lead_url: str | None,
) -> str:
    """Build a short Italian WhatsApp follow-up message (plain text).

    Kept intentionally brief and conversational — WA messages that read
    like marketing emails get flagged by Meta's spam filters. The link to
    the lead portal is included only when ``lead_url`` is available.

    Step 2 (~3 days after email): remind + invite question.
    Step 3+ (~11 days after email): last follow-up, soft close.
    """
    link_fragment = f"\n👉 {lead_url}" if lead_url else ""
    if step == 2:
        return (
            f"Buongiorno {greeting}! 👋\n"
            f"Qualche giorno fa le abbiamo inviato un'analisi del potenziale "
            f"fotovoltaico del suo immobile. Ha avuto modo di darci un'occhiata?\n"
            f"Sono qui per qualsiasi domanda — rispondo volentieri."
            f"{link_fragment}"
        )
    # step 3 or later
    return (
        f"Buongiorno {greeting},\n"
        f"un ultimo saluto da {tenant_name}. La nostra proposta sul risparmio "
        f"energetico è ancora attiva — se vuole approfondire o fissare una "
        f"chiamata, basta rispondere qui."
        f"{link_fragment}"
    )


def _log_api_cost(
    sb: Any,
    *,
    tenant_id: str,
    endpoint: str,
    cost_cents: int,
    status: str,
    metadata: dict[str, Any],
) -> None:
    try:
        sb.table("api_usage_log").insert(
            {
                "tenant_id": tenant_id,
                "provider": endpoint.split(":")[0],  # 'resend' or 'whatsapp'
                "endpoint": endpoint,
                "request_count": 1,
                "cost_cents": cost_cents,
                "status": status,
                "metadata": metadata,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("outreach.api_usage_log_failed", err=str(exc))


# ---------------------------------------------------------------------------
# Monthly budget helpers
# ---------------------------------------------------------------------------


async def _tenant_has_inboxes(sb: Any, tenant_id: str) -> bool:
    """Return True if the tenant has at least one active inbox configured.

    Used to distinguish "no inboxes at all → fall back to legacy" from
    "inboxes exist but all capped/paused → skip this send attempt".
    Cached in the worker process for 60 s via a simple module-level dict
    to avoid a DB round-trip on every send.
    """
    import time as _time

    _cache = _tenant_has_inboxes._cache  # type: ignore[attr-defined]
    entry = _cache.get(tenant_id)
    if entry and _time.monotonic() - entry[0] < 60:
        return entry[1]
    try:
        res = (
            sb.table("tenant_inboxes")
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            .eq("active", True)
            .limit(1)
            .execute()
        )
        result = bool(res.count and res.count > 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("outreach.has_inboxes_check_failed", tenant_id=tenant_id, err=str(exc))
        result = False
    _cache[tenant_id] = (_time.monotonic(), result)
    return result


_tenant_has_inboxes._cache: dict[str, tuple[float, bool]] = {}  # type: ignore[attr-defined]


async def _monthly_outreach_budget(sb: Any, tenant_id: str) -> float | None:
    """Return the tenant's monthly outreach budget (€) from the economico module.

    Returns None (= unlimited) when the module is absent or budget is 0.
    Uses a direct DB read rather than the async get_for_tenant() to keep
    the outreach agent's single-event execution path lean.
    """
    try:
        res = (
            sb.table("tenant_modules")
            .select("config")
            .eq("tenant_id", tenant_id)
            .eq("module_key", "economico")
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        cfg = res.data[0].get("config") or {}
        budget = cfg.get("budget_outreach_eur_month")
        return float(budget) if budget else None
    except Exception as exc:  # noqa: BLE001
        log.warning("outreach.budget_lookup_failed", tenant_id=tenant_id, err=str(exc))
        return None


async def _monthly_campaign_spend_cents(sb: Any, tenant_id: str) -> int:
    """Sum campaigns.cost_cents for the tenant in the current calendar month.

    Returns 0 on any DB error (fail-open: better to send than to permanently
    block outreach due to a transient DB issue).
    """
    from datetime import date

    first_of_month = date.today().replace(day=1).isoformat()
    try:
        res = (
            sb.table("outreach_sends")
            .select("cost_cents")
            .eq("tenant_id", tenant_id)
            .gte("created_at", first_of_month)
            .execute()
        )
        return sum(int(r.get("cost_cents") or 0) for r in (res.data or []))
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "outreach.monthly_spend_lookup_failed", tenant_id=tenant_id, err=str(exc)
        )
        return 0
