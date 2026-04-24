"""Tracking Agent — consumes provider webhook events and progresses leads.

Scope as of this release: Resend (email) + Pixart (postal). 360dialog
WhatsApp webhook is wired at the route layer (see routes/webhooks.py)
but its provider branch here still returns `provider_unsupported` — the
outbound/tracking side is the next phase. Stripe events are out of
scope entirely (tier activation is manual, see
apps/dashboard/src/lib/data/tier.ts).

Pipeline (Resend):

    webhook route verifies signature → enqueues ``tracking_task``
        with payload = {"provider": "resend", "event_type": "...",
                        "raw_payload": <normalised Resend JSON>}
        ↓
    parse_webhook_event(raw) → EmailEvent(id, type, email_id, to, ...)
        ↓
    idempotency: if an ``events`` row already has this Svix id → skip
        (keeps webhook retries from mutating state twice)
        ↓
    match campaigns row on email_message_id (both the outreach row and
        any follow-up steps share the same Resend id per send)
        ↓
    resolve (tenant_id, lead_id) from the campaigns row
        ↓
    apply transition → lead fields + optional status bump
        ↓
    record event in ``events`` table (audit trail + dedupe key)
        ↓
    for hard bounces/complaints: enqueue compliance_task so the pii_hash
        hits the global_blacklist and any queued follow-ups are cancelled.

All Resend-derived event types map cleanly:

    ``delivered``   → outreach_delivered_at + pipeline_status='delivered'
    ``opened``      → outreach_opened_at    + pipeline_status='opened'
    ``clicked``     → outreach_clicked_at   + pipeline_status='clicked'
    ``bounced``     → campaigns.status='failed', pipeline_status='blacklisted'
                      + enqueue compliance (BlacklistReason.BOUNCE_HARD)
    ``complained``  → campaigns.status='failed', pipeline_status='blacklisted'
                      + enqueue compliance (BlacklistReason.COMPLAINT)
    ``delivery_delayed`` → informational event only (no status flip)
    ``sent``        → informational; the outreach agent already set this
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from ..models.enums import BlacklistReason, CampaignStatus, LeadStatus
from ..services.reputation_enforcement_service import (
    check_realtime_bounce_spike,
    check_realtime_complaint_cluster,
)
from .base import AgentBase

log = get_logger(__name__)


class TrackingInput(BaseModel):
    provider: str                        # resend | pixart | whatsapp
    event_type: str                      # provider-native event type
    raw_payload: dict[str, Any]


class TrackingOutput(BaseModel):
    processed: bool = True
    lead_id: str | None = None
    campaign_id: str | None = None    # outreach_sends row id
    new_status: str | None = None
    skipped: bool = False
    reason: str | None = None


# ---------------------------------------------------------------------------
# Event → lead-field projection (pure, fully unit-testable)
# ---------------------------------------------------------------------------


# Keys are the *normalised* event types (after parse_webhook_event).
# Values describe how the lead row should move.
PIXART_TRANSITIONS: dict[str, dict[str, Any]] = {
    # Pixart event semantics:
    #   printed   → postcard produced (informational, no pipeline advance)
    #   shipped   → handed off to Poste Italiane (informational)
    #   delivered → postcard reached the subject's mailbox
    #   returned  → undeliverable, postal equivalent of a bounce
    "printed": {
        "lead_column": None,
        "pipeline_status": None,
        "campaign_status": None,
    },
    "shipped": {
        "lead_column": None,
        "pipeline_status": None,
        "campaign_status": None,
    },
    "delivered": {
        # Postal "delivered" shares the email-delivered timestamp column
        # because pipeline_status semantics overlap — delivered is delivered,
        # regardless of channel.
        "lead_column": "outreach_delivered_at",
        "pipeline_status": LeadStatus.DELIVERED.value,
        "campaign_status": CampaignStatus.DELIVERED.value,
    },
    "returned": {
        "lead_column": None,
        # Returned postcards are NOT treated as blacklist (wrong address
        # is often a data-quality issue, not a consent signal). Campaign
        # flips to failed but the lead stays in pipeline.
        "pipeline_status": None,
        "campaign_status": CampaignStatus.FAILED.value,
    },
}


RESEND_TRANSITIONS: dict[str, dict[str, Any]] = {
    "delivered": {
        "lead_column": "outreach_delivered_at",
        "pipeline_status": LeadStatus.DELIVERED.value,
        "campaign_status": CampaignStatus.DELIVERED.value,
    },
    "opened": {
        "lead_column": "outreach_opened_at",
        "pipeline_status": LeadStatus.OPENED.value,
        "campaign_status": None,
    },
    "clicked": {
        "lead_column": "outreach_clicked_at",
        "pipeline_status": LeadStatus.CLICKED.value,
        "campaign_status": None,
    },
    "bounced": {
        "lead_column": None,
        "pipeline_status": LeadStatus.BLACKLISTED.value,
        "campaign_status": CampaignStatus.FAILED.value,
        "blacklist": BlacklistReason.BOUNCE_HARD.value,
    },
    "complained": {
        "lead_column": None,
        "pipeline_status": LeadStatus.BLACKLISTED.value,
        "campaign_status": CampaignStatus.FAILED.value,
        "blacklist": BlacklistReason.COMPLAINT.value,
    },
    "sent": {
        "lead_column": None,
        "pipeline_status": None,
        "campaign_status": None,
    },
    "delivery_delayed": {
        "lead_column": None,
        "pipeline_status": None,
        "campaign_status": None,
    },
}


# Pipeline status order is monotonic — an 'opened' event shouldn't roll
# the status backward from 'clicked'. We encode the rank so we can apply
# ``max(existing, new)``.
_PIPELINE_RANK: dict[str, int] = {
    LeadStatus.NEW.value: 0,
    LeadStatus.SENT.value: 1,
    LeadStatus.DELIVERED.value: 2,
    LeadStatus.OPENED.value: 3,
    LeadStatus.CLICKED.value: 4,
    LeadStatus.ENGAGED.value: 5,
    LeadStatus.WHATSAPP.value: 6,
    LeadStatus.APPOINTMENT.value: 7,
    LeadStatus.CLOSED_WON.value: 8,
    LeadStatus.CLOSED_LOST.value: 8,
    LeadStatus.BLACKLISTED.value: 99,
}


def project_resend_lead_update(
    *, event_type: str, current_status: str | None, occurred_at: str | None
) -> dict[str, Any]:
    """Pure: compute the ``UPDATE leads SET ...`` dict for one webhook event.

    Returns an empty dict when the event doesn't advance the lead — e.g.
    an ``opened`` event for an already-clicked lead. The webhook route
    can detect a no-op and skip the round-trip entirely.
    """
    transition = RESEND_TRANSITIONS.get(event_type)
    if not transition:
        return {}

    update: dict[str, Any] = {}
    lead_col = transition.get("lead_column")
    if lead_col and occurred_at:
        update[lead_col] = occurred_at

    new_status = transition.get("pipeline_status")
    if new_status:
        current_rank = _PIPELINE_RANK.get(current_status or "", 0)
        new_rank = _PIPELINE_RANK.get(new_status, 0)
        # Blacklist is terminal — always wins.
        if new_status == LeadStatus.BLACKLISTED.value:
            update["pipeline_status"] = new_status
        elif new_rank > current_rank:
            update["pipeline_status"] = new_status

    return update


def project_resend_campaign_update(event_type: str) -> dict[str, Any]:
    """Pure: return the campaigns update dict for one webhook event."""
    transition = RESEND_TRANSITIONS.get(event_type)
    if not transition:
        return {}
    out: dict[str, Any] = {}
    cstatus = transition.get("campaign_status")
    if cstatus:
        out["status"] = cstatus
    if event_type in {"bounced", "complained"}:
        out["failure_reason"] = event_type
    return out


def project_pixart_lead_update(
    *, event_type: str, current_status: str | None, occurred_at: str | None
) -> dict[str, Any]:
    """Pure: compute ``UPDATE leads SET ...`` for a Pixart webhook event.

    Mirrors ``project_resend_lead_update`` but against ``PIXART_TRANSITIONS``.
    Monotonic pipeline rule applies identically: a postal 'delivered' on an
    already-opened lead lands the timestamp but doesn't roll back pipeline.
    """
    transition = PIXART_TRANSITIONS.get(event_type)
    if not transition:
        return {}

    update: dict[str, Any] = {}
    lead_col = transition.get("lead_column")
    if lead_col and occurred_at:
        update[lead_col] = occurred_at

    new_status = transition.get("pipeline_status")
    if new_status:
        current_rank = _PIPELINE_RANK.get(current_status or "", 0)
        new_rank = _PIPELINE_RANK.get(new_status, 0)
        if new_rank > current_rank:
            update["pipeline_status"] = new_status

    return update


def project_pixart_campaign_update(event_type: str) -> dict[str, Any]:
    """Pure: return the campaigns update dict for a Pixart webhook event."""
    transition = PIXART_TRANSITIONS.get(event_type)
    if not transition:
        return {}
    out: dict[str, Any] = {}
    cstatus = transition.get("campaign_status")
    if cstatus:
        out["status"] = cstatus
    if event_type == "returned":
        out["failure_reason"] = "postal_returned"
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_domain_for_inbox(sb: Any, inbox_id: str | None) -> tuple[str | None, str]:
    """Synchronous: return (domain_id, domain_name) for a given inbox row.

    Used by real-time reputation checks.  Returns ``(None, "")`` on any
    error so callers gracefully fall through to tenant-level lookups.
    """
    if not inbox_id:
        return None, ""
    try:
        res = (
            sb.table("tenant_inboxes")
            .select("domain_id, tenant_email_domains(id, domain)")
            .eq("id", inbox_id)
            .limit(1)
            .execute()
        )
        row = (res.data or [None])[0]
        if not row:
            return None, ""
        dom = row.get("tenant_email_domains") or {}
        if isinstance(dom, list):
            dom = dom[0] if dom else {}
        return dom.get("id"), str(dom.get("domain") or "")
    except Exception as exc:  # noqa: BLE001
        log.debug(
            "tracking.resolve_domain_failed",
            inbox_id=inbox_id,
            err=str(exc),
        )
        return None, ""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class TrackingAgent(AgentBase[TrackingInput, TrackingOutput]):
    name = "agent.tracking"

    async def execute(self, payload: TrackingInput) -> TrackingOutput:  # noqa: C901
        if payload.provider == "pixart":
            return await self._execute_pixart(payload)
        if payload.provider != "resend":
            # Sprint 7+ will handle other providers; just audit for now.
            await self._emit_event(
                event_type=f"tracking.{payload.provider}.received",
                payload={"event_type": payload.event_type},
            )
            return TrackingOutput(processed=False, skipped=True, reason="provider_unsupported")

        # The webhook route is expected to have already normalised the
        # payload through resend_service.parse_webhook_event — but we
        # defensively re-parse in case a test calls the agent directly.
        from ..services.resend_service import parse_webhook_event

        event = parse_webhook_event(payload.raw_payload)
        sb = get_service_client()

        # -------------------------------------------------------------
        # 1) Idempotency — Resend retries on 5xx responses; the Svix
        # message id is the dedupe key.
        # -------------------------------------------------------------
        if event.id:
            existing = (
                sb.table("events")
                .select("id")
                .eq("payload->>svix_id", event.id)
                .limit(1)
                .execute()
            )
            if existing.data:
                return TrackingOutput(
                    processed=False,
                    skipped=True,
                    reason="duplicate_svix_id",
                )

        # -------------------------------------------------------------
        # 2) Resolve campaign → (tenant_id, lead_id). Without a match
        # we still audit the event but can't advance any lead.
        # -------------------------------------------------------------
        campaign_res = (
            sb.table("outreach_sends")
            .select("id, tenant_id, lead_id, status, inbox_id")
            .eq("email_message_id", event.email_id)
            .limit(1)
            .execute()
        )
        campaign = (campaign_res.data or [None])[0]
        if not campaign:
            await self._emit_event(
                event_type=f"tracking.resend.orphan_{event.type}",
                payload={
                    "svix_id": event.id,
                    "email_id": event.email_id,
                    "type": event.type,
                },
            )
            return TrackingOutput(
                processed=False,
                skipped=True,
                reason="no_matching_campaign",
            )

        tenant_id = campaign["tenant_id"]
        lead_id = campaign["lead_id"]

        # -------------------------------------------------------------
        # 3) Apply lead-row update (if any)
        # -------------------------------------------------------------
        lead_res = (
            sb.table("leads")
            .select("id, pipeline_status, subject_id")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        lead_row = (lead_res.data or [None])[0]
        current_status = (lead_row or {}).get("pipeline_status")

        lead_update = project_resend_lead_update(
            event_type=event.type,
            current_status=current_status,
            occurred_at=event.occurred_at,
        )
        if lead_update:
            sb.table("leads").update(lead_update).eq("id", lead_id).execute()

        # -------------------------------------------------------------
        # 4) Apply campaigns-row update (if any)
        # -------------------------------------------------------------
        campaign_update = project_resend_campaign_update(event.type)
        if campaign_update:
            sb.table("outreach_sends").update(campaign_update).eq(
                "id", campaign["id"]
            ).execute()

        # -------------------------------------------------------------
        # 5) Bounces / complaints → hand off to compliance agent
        # -------------------------------------------------------------
        blacklist_reason = RESEND_TRANSITIONS.get(event.type, {}).get("blacklist")
        if blacklist_reason and lead_row:
            await self._enqueue_compliance(
                sb=sb,
                tenant_id=tenant_id,
                subject_id=lead_row["subject_id"],
                reason=blacklist_reason,
                source=f"resend.{event.type}",
            )

        # -------------------------------------------------------------
        # 5b) Real-time reputation enforcement (Sprint 6.5)
        #
        #   complained → complaint-cluster guard: if ≥3 complaints from
        #     the same tenant domain within 60 min → immediate 48h pause.
        #   bounced    → bounce-spike guard: if rolling 24h bounce rate
        #     exceeds 8% and at least 10 sends in the window → pause.
        #
        # Both checks are non-fatal: any exception is swallowed to
        # ensure the webhook ACK is never delayed by a reputation check.
        # -------------------------------------------------------------
        if event.type in {"complained", "bounced"}:
            domain_id, domain_name = _resolve_domain_for_inbox(
                sb, campaign.get("inbox_id")
            )
            if event.type == "complained":
                try:
                    await check_realtime_complaint_cluster(
                        sb,
                        tenant_id=tenant_id,
                        domain_id=domain_id,
                        domain_name=domain_name,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "tracking.complaint_cluster_check_failed",
                        tenant_id=tenant_id,
                        err=str(exc),
                    )
            else:  # bounced
                try:
                    await check_realtime_bounce_spike(
                        sb,
                        tenant_id=tenant_id,
                        domain_id=domain_id,
                        domain_name=domain_name,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "tracking.bounce_spike_check_failed",
                        tenant_id=tenant_id,
                        err=str(exc),
                    )

        # -------------------------------------------------------------
        # 6) Audit event (also the dedupe marker for future calls)
        # -------------------------------------------------------------
        await self._emit_event(
            event_type=f"lead.email_{event.type}",
            payload={
                "svix_id": event.id,
                "email_id": event.email_id,
                "type": event.type,
                "to": event.to,
                "occurred_at": event.occurred_at,
            },
            tenant_id=tenant_id,
            lead_id=lead_id,
        )

        return TrackingOutput(
            processed=True,
            lead_id=lead_id,
            campaign_id=campaign["id"],
            new_status=lead_update.get("pipeline_status"),
        )

    # ------------------------------------------------------------------
    # Pixart (postal) branch
    # ------------------------------------------------------------------

    async def _execute_pixart(self, payload: TrackingInput) -> TrackingOutput:
        """Handle one Pixart postal webhook event.

        Symmetric to the Resend branch: resolve campaign by tracking
        number (falling back to provider order id), project lead + campaign
        updates, emit a ``lead.postal_{event_type}`` audit event.
        """
        raw = payload.raw_payload
        event_type = (payload.event_type or "").strip().lower()

        tracking_id = (
            raw.get("tracking_number")
            or raw.get("tracking_code")
            or raw.get("trackingId")
            or ""
        )
        order_id = (
            raw.get("order_id")
            or raw.get("orderId")
            or raw.get("provider_order_id")
            or ""
        )
        occurred_at = (
            raw.get("occurred_at")
            or raw.get("timestamp")
            or raw.get("event_date")
            or None
        )

        if not tracking_id and not order_id:
            await self._emit_event(
                event_type=f"tracking.pixart.no_identifier_{event_type or 'unknown'}",
                payload={"keys": list(raw.keys())},
            )
            return TrackingOutput(
                processed=False, skipped=True, reason="no_tracking_identifier"
            )

        sb = get_service_client()

        # ------------------------------------------------------------
        # 1) Resolve campaign by tracking_number first, then order_id.
        # ------------------------------------------------------------
        campaign: dict[str, Any] | None = None
        if tracking_id:
            res = (
                sb.table("outreach_sends")
                .select("id, tenant_id, lead_id, status")
                .eq("postal_tracking_number", tracking_id)
                .limit(1)
                .execute()
            )
            campaign = (res.data or [None])[0]
        if not campaign and order_id:
            res = (
                sb.table("outreach_sends")
                .select("id, tenant_id, lead_id, status")
                .eq("postal_provider_order_id", order_id)
                .limit(1)
                .execute()
            )
            campaign = (res.data or [None])[0]

        if not campaign:
            await self._emit_event(
                event_type=f"tracking.pixart.orphan_{event_type or 'unknown'}",
                payload={
                    "tracking_id": tracking_id or None,
                    "order_id": order_id or None,
                },
            )
            return TrackingOutput(
                processed=False, skipped=True, reason="no_matching_campaign"
            )

        tenant_id = campaign["tenant_id"]
        lead_id = campaign["lead_id"]

        # ------------------------------------------------------------
        # 2) Apply lead-row update (if any)
        # ------------------------------------------------------------
        lead_res = (
            sb.table("leads")
            .select("id, pipeline_status")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        lead_row = (lead_res.data or [None])[0]
        current_status = (lead_row or {}).get("pipeline_status")

        lead_update = project_pixart_lead_update(
            event_type=event_type,
            current_status=current_status,
            occurred_at=occurred_at,
        )
        if lead_update:
            sb.table("leads").update(lead_update).eq("id", lead_id).execute()

        # ------------------------------------------------------------
        # 3) Apply campaigns-row update (if any)
        # ------------------------------------------------------------
        campaign_update = project_pixart_campaign_update(event_type)
        if campaign_update:
            sb.table("outreach_sends").update(campaign_update).eq(
                "id", campaign["id"]
            ).execute()

        # ------------------------------------------------------------
        # 4) Audit event
        # ------------------------------------------------------------
        await self._emit_event(
            event_type=f"lead.postal_{event_type}" if event_type else "lead.postal_unknown",
            payload={
                "tracking_id": tracking_id or None,
                "order_id": order_id or None,
                "event_type": event_type,
                "occurred_at": occurred_at,
            },
            tenant_id=tenant_id,
            lead_id=lead_id,
        )

        return TrackingOutput(
            processed=True,
            lead_id=lead_id,
            campaign_id=campaign["id"],
            new_status=lead_update.get("pipeline_status"),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _enqueue_compliance(
        self,
        *,
        sb: Any,
        tenant_id: str,
        subject_id: str,
        reason: str,
        source: str,
    ) -> None:
        """Look up pii_hash for the subject and enqueue a compliance run."""
        try:
            subj_res = (
                sb.table("subjects")
                .select("pii_hash")
                .eq("id", subject_id)
                .limit(1)
                .execute()
            )
            pii_hash = (
                (subj_res.data or [{}])[0].get("pii_hash") if subj_res.data else None
            )
            if not pii_hash:
                return
            from ..core.queue import enqueue

            await enqueue(
                "compliance_task",
                {
                    "pii_hash": pii_hash,
                    "reason": reason,
                    "source": source,
                },
                job_id=f"compliance:{pii_hash}:{reason}",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "tracking.compliance_enqueue_failed",
                tenant_id=tenant_id,
                err=str(exc),
            )
