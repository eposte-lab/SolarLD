"""Domain reputation digest — nightly rollup of send/bounce/complaint.

Writes one row per (tenant_id, email_from_domain, as_of_date) into
``domain_reputation`` on each run. The dashboard's ``/settings``
reputation card reads the latest row; the alarm flags are precomputed
here so the UI doesn't re-derive thresholds.

Thresholds (source: AWS SES + Resend published guidance):

    bounce_rate    > 0.05  → warning (yellow)
    bounce_rate    > 0.10  → critical — SES suspends accounts here
    complaint_rate > 0.003 → warning
    complaint_rate > 0.005 → critical — SES suspends accounts here

We flatten to a single boolean per metric (warning OR critical). The
dashboard decides colour based on the precise rate. That keeps this
table cheap and lets UX evolve without a backfill.

Window: 7 days ending at ``as_of_date`` (inclusive). Counts are
tenant-scoped (not cross-tenant) so shared providers like a dedicated
Resend subdomain per tenant still attribute correctly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from ..models.enums import CampaignStatus, OutreachChannel

log = get_logger(__name__)

# Rolling window length (days) the digest aggregates over.
WINDOW_DAYS = 7

# Alarm thresholds — kept in lockstep with dashboard copy.
BOUNCE_WARN = 0.05
COMPLAINT_WARN = 0.003


@dataclass
class DomainDigest:
    """Per-tenant rollup for one (domain, as_of_date) snapshot."""

    tenant_id: str
    email_from_domain: str
    sent_count: int = 0
    delivered_count: int = 0
    bounced_count: int = 0
    complained_count: int = 0
    opened_count: int = 0
    bounced_leads: set[str] = field(default_factory=set)
    complained_leads: set[str] = field(default_factory=set)
    opened_leads: set[str] = field(default_factory=set)


async def run_reputation_digest(
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Compute and persist today's reputation snapshot for every tenant.

    ``as_of`` defaults to today (UTC). Re-running the digest for the
    same date upserts (the table has a unique key on
    tenant/domain/date), so cron retries are idempotent.

    Returns ``{"rows": N, "alarms": M}`` for logging.
    """
    sb = get_service_client()
    today = as_of or datetime.now(timezone.utc).date()
    window_start = today - timedelta(days=WINDOW_DAYS - 1)
    window_start_iso = datetime.combine(
        window_start, datetime.min.time(), tzinfo=timezone.utc
    ).isoformat()

    # ------------------------------------------------------------------
    # 1) Tenant list with a configured domain — tenants that never
    #    wired their domain are out of scope (no reputation to track).
    # ------------------------------------------------------------------
    tenants_res = (
        sb.table("tenants")
        .select("id, email_from_domain")
        .not_.is_("email_from_domain", "null")
        .execute()
    )
    tenant_rows = tenants_res.data or []
    if not tenant_rows:
        log.info("reputation.digest.no_tenants")
        return {"rows": 0, "alarms": 0}

    digests: dict[str, DomainDigest] = {}
    for t in tenant_rows:
        domain = (t.get("email_from_domain") or "").strip().lower()
        if not domain:
            continue
        digests[t["id"]] = DomainDigest(
            tenant_id=t["id"], email_from_domain=domain
        )

    # ------------------------------------------------------------------
    # 2) Campaigns in window — sent + delivered counts.
    #    status='sent' counts as an attempt (we paid for Resend).
    #    'delivered' and 'failed' are both outcomes of attempts.
    # ------------------------------------------------------------------
    campaigns_res = (
        sb.table("campaigns")
        .select("tenant_id, lead_id, status")
        .eq("channel", OutreachChannel.EMAIL.value)
        .gte("sent_at", window_start_iso)
        .execute()
    )
    for row in campaigns_res.data or []:
        tid = row.get("tenant_id")
        if tid not in digests:
            continue
        d = digests[tid]
        status = row.get("status")
        if status in (
            CampaignStatus.SENT.value,
            CampaignStatus.DELIVERED.value,
            CampaignStatus.FAILED.value,
        ):
            d.sent_count += 1
        if status == CampaignStatus.DELIVERED.value:
            d.delivered_count += 1

    # ------------------------------------------------------------------
    # 3) Events in window — distinct leads per engagement kind.
    #
    # We count distinct ``lead_id`` (not events) because Resend can
    # emit multiple bounce/complaint events for the same message
    # (e.g. a bounce retry). Attributing reputation per-lead matches
    # ISP logic more closely.
    # ------------------------------------------------------------------
    events_res = (
        sb.table("events")
        .select("tenant_id, lead_id, event_type")
        .in_("event_type", [
            "lead.email_bounced",
            "lead.email_complained",
            "lead.email_opened",
        ])
        .gte("occurred_at", window_start_iso)
        .execute()
    )
    for ev in events_res.data or []:
        tid = ev.get("tenant_id")
        lid = ev.get("lead_id")
        if tid not in digests or not lid:
            continue
        d = digests[tid]
        etype = ev.get("event_type")
        if etype == "lead.email_bounced":
            d.bounced_leads.add(str(lid))
        elif etype == "lead.email_complained":
            d.complained_leads.add(str(lid))
        elif etype == "lead.email_opened":
            d.opened_leads.add(str(lid))

    # Finalize the set-based counts.
    for d in digests.values():
        d.bounced_count = len(d.bounced_leads)
        d.complained_count = len(d.complained_leads)
        d.opened_count = len(d.opened_leads)

    # ------------------------------------------------------------------
    # 4) Upsert snapshots. Use a single batched call — the table has
    #    a UNIQUE(tenant_id, email_from_domain, as_of_date), so the
    #    on_conflict clause resolves idempotency.
    # ------------------------------------------------------------------
    upserts: list[dict[str, Any]] = []
    alarms = 0
    for d in digests.values():
        delivery_rate = (
            d.delivered_count / d.sent_count if d.sent_count else None
        )
        bounce_rate = (
            d.bounced_count / d.sent_count if d.sent_count else None
        )
        complaint_rate = (
            d.complained_count / d.delivered_count
            if d.delivered_count
            else None
        )
        open_rate = (
            d.opened_count / d.delivered_count if d.delivered_count else None
        )
        alarm_bounce = bool(bounce_rate is not None and bounce_rate > BOUNCE_WARN)
        alarm_complaint = bool(
            complaint_rate is not None and complaint_rate > COMPLAINT_WARN
        )
        if alarm_bounce or alarm_complaint:
            alarms += 1
        upserts.append({
            "tenant_id": d.tenant_id,
            "email_from_domain": d.email_from_domain,
            "as_of_date": today.isoformat(),
            "sent_count": d.sent_count,
            "delivered_count": d.delivered_count,
            "bounced_count": d.bounced_count,
            "complained_count": d.complained_count,
            "opened_count": d.opened_count,
            "delivery_rate": delivery_rate,
            "bounce_rate": bounce_rate,
            "complaint_rate": complaint_rate,
            "open_rate": open_rate,
            "alarm_bounce": alarm_bounce,
            "alarm_complaint": alarm_complaint,
        })

    if upserts:
        # on_conflict is required because the daily cron may run twice
        # on the same date (e.g. manual re-trigger from admin).
        sb.table("domain_reputation").upsert(
            upserts,
            on_conflict="tenant_id,email_from_domain,as_of_date",
        ).execute()

    log.info(
        "reputation.digest.done",
        rows=len(upserts),
        alarms=alarms,
        window_start=window_start.isoformat(),
        as_of=today.isoformat(),
    )
    return {"rows": len(upserts), "alarms": alarms}
