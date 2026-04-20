"""Outreach Agent — sends first-contact email (B2B) via Resend.

Sprint 6 pipeline (postal B2C deferred to Sprint 8):

    lead_id + tenant_id
        ↓
    load lead + subject + roof + tenant (branding)
        ↓
    idempotency: if an email campaign already exists for this lead
        and outreach_sent_at is set → skip
        ↓
    compliance gate: if subject.pii_hash in global_blacklist → skip
        (the compliance agent normally cancels pending campaigns when
        a blacklist entry is added, but we also gate here in case an
        outreach job is enqueued before the blacklist propagation runs)
        ↓
    recipient resolution:
        channel=email  → subject.decision_maker_email
                        (only send if decision_maker_email_verified)
        channel=postal → abort with reason='postal_not_implemented'
        ↓
    optional: Claude writes a 1-sentence personalised opener
        (B2B references ATECO / business_name, B2C references address)
        ↓
    render_outreach_email(ctx) → (subject, html, text)
        ↓
    send_email(...) via Resend HTTP
        ↓
    INSERT campaigns (status='sent', email_message_id, cost_cents)
    UPDATE leads SET outreach_channel, outreach_sent_at,
        pipeline_status='sent'
        ↓
    emit lead.outreach_sent event

Degradation:
  * Missing verified email → we still insert a campaigns row with
    status='failed' and failure_reason='no_verified_email' so the
    dashboard can surface the reason. We don't crash the worker.
  * Claude opener failure → we skip the opener (templates degrade
    gracefully — the ``personalized_opener`` is optional in Jinja).
  * Resend 4xx → treated as permanent failure; campaigns row goes in
    with status='failed' and failure_reason pulled from the exception.
  * Resend 5xx → bubbles up from the service's tenacity retry. The
    worker's exponential retry will pick it up later.
"""

from __future__ import annotations

import random as _random
from datetime import datetime, timezone
from typing import Any

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
from ..services.rate_limit_service import acquire_email_quota
from ..services.resend_service import (
    RESEND_COST_PER_EMAIL_CENTS,
    ResendError,
    SendEmailInput,
    send_email,
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
        le=3,
        description=(
            "Which step of the sequence we're sending. 1 = initial "
            "outreach (OutreachAgent default), 2/3 = follow-ups enqueued "
            "by the follow-up cron."
        ),
    )


class OutreachOutput(BaseModel):
    lead_id: str
    campaign_id: str | None = None
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
                sb.table("campaigns")
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
        # 5) Channel routing — postal is Sprint 8
        # ------------------------------------------------------------------
        if payload.channel == OutreachChannel.POSTAL:
            return await self._record_skip(
                payload=payload,
                lead=lead,
                reason="postal_not_implemented",
            )

        # ------------------------------------------------------------------
        # 6) Recipient resolution
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
        lead_url = _public_lead_url(lead.get("public_slug"))
        optout_url = _optout_url(lead.get("public_slug"))
        default_subject = default_subject_for(
            subject_type,
            tenant_row.get("business_name") or "SolarLead",
            sequence_step=payload.sequence_step,
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

        # Read tenant's saved email style & copy overrides (B.14)
        t_settings: dict = dict(tenant_row.get("settings") or {})
        email_copy: dict = dict(t_settings.get("email_copy_overrides") or {})
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
        )
        rendered = render_outreach_email(ctx)

        # ------------------------------------------------------------------
        # 8) Deliverability rate-limit — protect sender reputation
        #
        # Two caps stacked behind a single call:
        #   * warm-up daily cap (20/50/.../2000) if the domain is less
        #     than 7 days old or has never been verified.
        #   * steady-state hourly cap (tier default or per-tenant
        #     settings.email_rate_per_hour override) otherwise.
        #
        # On cap hit we *don't* create a campaigns row (unlike a send
        # failure) — the skip is expected to retry on the next window.
        # The follow-up cron re-evaluates candidates daily, so step-2/3
        # naturally roll forward. Step-1 will retry when whatever
        # originally enqueued it (CreativeAgent / manual dashboard
        # action) re-enqueues.
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
        # 9) Send via Resend
        # ------------------------------------------------------------------
        from_address = _build_from_address(tenant_row)
        send_input = SendEmailInput(
            from_address=from_address,
            to=[recipient],
            subject=rendered.subject,
            html=rendered.html,
            text=rendered.text,
            reply_to=_build_reply_to(tenant_row, lead.get("public_slug")),
            tags={
                "tenant_id": payload.tenant_id,
                "lead_id": payload.lead_id,
                "template": _template_id_for(subject_type),
            },
        )

        try:
            send_result = await send_email(send_input)
        except ResendError as exc:
            log.warning(
                "outreach.resend_failed",
                lead_id=payload.lead_id,
                err=str(exc),
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
            "email_message_id": send_result.id,
            "email_subject": rendered.subject,
            "scheduled_for": now_iso,
            "sent_at": now_iso,
            "cost_cents": RESEND_COST_PER_EMAIL_CENTS,
            "status": CampaignStatus.SENT.value,
        }
        if experiment_id and experiment_variant:
            campaign_insert["experiment_id"] = experiment_id
            campaign_insert["experiment_variant"] = experiment_variant
        campaign_res = (
            sb.table("campaigns").insert(campaign_insert).execute()
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
                "message_id": send_result.id,
            },
        )

        out = OutreachOutput(
            lead_id=payload.lead_id,
            campaign_id=campaign_id,
            provider_id=send_result.id,
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
            "channel": OutreachChannel.EMAIL.value,
            "template_id": _template_id_for(
                subject_type, sequence_step=payload.sequence_step
            ),
            "sequence_step": payload.sequence_step,
            "scheduled_for": now_iso,
            "cost_cents": 0,
            "status": CampaignStatus.FAILED.value,
            "failure_reason": failure_reason,
        }
        res = sb.table("campaigns").insert(failure_insert).execute()
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


def _public_lead_url(public_slug: str | None) -> str:
    from ..core.config import settings

    base = (settings.next_public_lead_portal_url or "").rstrip("/")
    slug = (public_slug or "").strip()
    return f"{base}/l/{slug}" if slug else base


def _optout_url(public_slug: str | None) -> str:
    from ..core.config import settings

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
                "provider": "resend",
                "endpoint": endpoint,
                "request_count": 1,
                "cost_cents": cost_cents,
                "status": status,
                "metadata": metadata,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("outreach.api_usage_log_failed", err=str(exc))
