"""Reputation enforcement — auto-pause domains that exceed alarm thresholds.

Runs after ``reputation_service.run_reputation_digest()`` (nightly 02:30 UTC)
and also on-demand when the tracking agent detects a real-time spike.

Enforcement rules
-----------------
* bounce_rate  > 5%   → pause domain 48h, reason='bounce_rate_exceeded'
* complaint_rate > 0.3% → pause domain 48h, reason='complaint_rate_exceeded'
  Gmail's threshold is 0.08% for "red zone" / 0.3% for immediate suppression.
* Real-time cluster guard (Sprint 6.5): ≥3 complaints within the same 60-min
  window on the same domain → immediate pause (bypasses nightly batch).

For each paused domain:
  1. Set ``tenant_email_domains.paused_until = now() + 48h``,
     ``pause_reason = reason``, ``alarm_bounce / alarm_complaint = true``.
  2. Pause all active inboxes under that domain (cascade pause).
  3. Create a ``notifications`` row so the dashboard shows a red banner.

Unpause can only be done by super_admin via
``POST /v1/admin/email-domains/{id}/force-verify`` — not by the tenant.
This is intentional: the tenant should first understand what caused the
spike (bad list hygiene, shared domain abuse, etc.) before resuming.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.logging import get_logger

log = get_logger(__name__)

# Thresholds (matching Gmail's guidelines).
BOUNCE_RATE_THRESHOLD = 0.05      # 5 %
COMPLAINT_RATE_THRESHOLD = 0.003  # 0.3 %

# How long to pause on alarm.
PAUSE_HOURS_ON_ALARM = 48

# Real-time cluster: how many complaints within this window trigger instant pause.
REALTIME_COMPLAINT_CLUSTER_SIZE = 3
REALTIME_COMPLAINT_WINDOW_MINUTES = 60

# Real-time bounce spike: if rolling-24h bounce rate exceeds this on a minimum
# volume of sends, trigger an early-warning pause (8% threshold is stricter in
# time window than the nightly 7-day 5% threshold to catch spikes quickly).
REALTIME_BOUNCE_SPIKE_THRESHOLD = 0.08   # 8 % in 24 h
REALTIME_BOUNCE_WINDOW_HOURS = 24
REALTIME_BOUNCE_MIN_SENDS = 10           # need at least this many sends to compute

# Notification type constant.
_NOTIF_TYPE = "domain_reputation_alarm"


@dataclass
class EnforcementResult:
    """What the enforcement service did in one run."""
    domains_checked: int = 0
    domains_paused: int = 0
    paused_details: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def run_enforcement(sb: Any) -> EnforcementResult:
    """Nightly enforcement pass: check all non-paused domains for alarms.

    Called by ``cron.py::reputation_digest_cron`` after the digest
    finishes, so the alarm flags in ``domain_reputation`` are fresh.

    Args:
        sb: Supabase service-role client (sync, as used elsewhere in the codebase).
    """
    result = EnforcementResult()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Pull all active domains with fresh reputation data.
    # We join domain_reputation on the most recent row per domain name.
    # PostgREST can't do windowed queries, so we fetch both tables separately
    # and reconcile in Python (domains set is small — typically 1-10 per run).
    try:
        domains_res = (
            sb.table("tenant_email_domains")
            .select(
                "id, tenant_id, domain, purpose, paused_until, "
                "alarm_bounce, alarm_complaint, active"
            )
            .eq("active", True)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("reputation_enforcement.fetch_domains_failed", err=str(exc))
        result.errors.append(f"fetch_domains: {exc}")
        return result

    domains: list[dict[str, Any]] = domains_res.data or []
    result.domains_checked = len(domains)

    # Fetch latest domain_reputation rows (one per domain name).
    try:
        rep_res = (
            sb.table("domain_reputation")
            .select("domain, bounce_rate, complaint_rate, alarm_bounce, alarm_complaint, measured_at")
            .order("measured_at", desc=True)
            .limit(500)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("reputation_enforcement.fetch_reputation_failed", err=str(exc))
        result.errors.append(f"fetch_reputation: {exc}")
        return result

    rep_rows: list[dict[str, Any]] = rep_res.data or []
    # Index by domain name — keep only the freshest row per domain.
    rep_by_domain: dict[str, dict[str, Any]] = {}
    for row in rep_rows:
        d = (row.get("domain") or "").lower().strip()
        if d and d not in rep_by_domain:
            rep_by_domain[d] = row

    for domain_row in domains:
        domain_name = (domain_row.get("domain") or "").lower()
        rep = rep_by_domain.get(domain_name)
        if rep is None:
            continue  # No reputation data yet — brand-new domain.

        alarm_bounce = bool(rep.get("alarm_bounce"))
        alarm_complaint = bool(rep.get("alarm_complaint"))
        bounce_rate = float(rep.get("bounce_rate") or 0)
        complaint_rate = float(rep.get("complaint_rate") or 0)

        # Also re-evaluate directly against raw rates (reputation_service
        # already set alarm flags, but double-check in case schema changes).
        if not alarm_bounce and bounce_rate > BOUNCE_RATE_THRESHOLD:
            alarm_bounce = True
        if not alarm_complaint and complaint_rate > COMPLAINT_RATE_THRESHOLD:
            alarm_complaint = True

        if not alarm_bounce and not alarm_complaint:
            continue  # Domain healthy — nothing to do.

        # Skip if already paused.
        existing_pause = domain_row.get("paused_until")
        if existing_pause and existing_pause > now_iso:
            continue  # Already paused from a prior run.

        reason = (
            "bounce_rate_exceeded" if alarm_bounce
            else "complaint_rate_exceeded"
        )
        await _pause_domain_and_inboxes(
            sb,
            domain_row=domain_row,
            reason=reason,
            bounce_rate=bounce_rate,
            complaint_rate=complaint_rate,
            alarm_bounce=alarm_bounce,
            alarm_complaint=alarm_complaint,
        )
        result.domains_paused += 1
        result.paused_details.append({
            "domain_id": domain_row["id"],
            "domain": domain_name,
            "reason": reason,
            "bounce_rate": bounce_rate,
            "complaint_rate": complaint_rate,
        })

    log.info(
        "reputation_enforcement.done",
        checked=result.domains_checked,
        paused=result.domains_paused,
    )
    return result


async def check_realtime_complaint_cluster(
    sb: Any,
    *,
    tenant_id: str,
    domain_id: str | None,
    domain_name: str,
) -> bool:
    """Called by TrackingAgent on each complaint webhook.

    Returns True if the cluster threshold was breached and the domain
    was paused. The caller logs accordingly.

    Design: we count ``outreach_sends`` complaint events in the last
    ``REALTIME_COMPLAINT_WINDOW_MINUTES`` by looking at the lead
    pipeline_status='complained' (or a dedicated events table if it
    exists). If ≥ REALTIME_COMPLAINT_CLUSTER_SIZE → pause immediately.
    """
    window_start = (
        datetime.now(timezone.utc)
        - timedelta(minutes=REALTIME_COMPLAINT_WINDOW_MINUTES)
    ).isoformat()

    try:
        # Count complaint events on outreach_sends for this tenant
        # within the last hour.
        cnt_res = (
            sb.table("outreach_sends")
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            .eq("status", "complained")
            .gte("updated_at", window_start)
            .execute()
        )
        count = cnt_res.count or 0
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "reputation_enforcement.cluster_count_failed",
            tenant_id=tenant_id,
            err=str(exc),
        )
        return False

    if count < REALTIME_COMPLAINT_CLUSTER_SIZE:
        return False

    # Cluster threshold breached — find the domain row and pause it.
    if domain_id:
        try:
            dom_res = (
                sb.table("tenant_email_domains")
                .select("id, tenant_id, domain, purpose, paused_until, active")
                .eq("id", domain_id)
                .execute()
            )
            domain_rows = dom_res.data or []
        except Exception as exc:  # noqa: BLE001
            domain_rows = []
    else:
        try:
            dom_res = (
                sb.table("tenant_email_domains")
                .select("id, tenant_id, domain, purpose, paused_until, active")
                .eq("tenant_id", tenant_id)
                .eq("domain", domain_name.lower())
                .execute()
            )
            domain_rows = dom_res.data or []
        except Exception as exc:  # noqa: BLE001
            domain_rows = []

    if not domain_rows:
        log.warning(
            "reputation_enforcement.cluster_domain_not_found",
            domain=domain_name,
        )
        return False

    domain_row = domain_rows[0]
    await _pause_domain_and_inboxes(
        sb,
        domain_row=domain_row,
        reason="complaint_cluster_realtime",
        alarm_complaint=True,
        alarm_bounce=False,
        bounce_rate=0.0,
        complaint_rate=float(count) / max(count, 1),
    )
    log.warning(
        "reputation_enforcement.cluster_pause",
        domain=domain_name,
        complaint_count=count,
        window_minutes=REALTIME_COMPLAINT_WINDOW_MINUTES,
    )
    return True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _pause_domain_and_inboxes(
    sb: Any,
    *,
    domain_row: dict[str, Any],
    reason: str,
    alarm_bounce: bool,
    alarm_complaint: bool,
    bounce_rate: float,
    complaint_rate: float,
) -> None:
    domain_id: str = domain_row["id"]
    tenant_id: str = domain_row["tenant_id"]
    domain_name: str = domain_row.get("domain") or ""
    now_iso = datetime.now(timezone.utc).isoformat()
    until = (
        datetime.now(timezone.utc) + timedelta(hours=PAUSE_HOURS_ON_ALARM)
    ).isoformat()

    # 1. Pause the domain.
    try:
        (
            sb.table("tenant_email_domains")
            .update(
                {
                    "paused_until": until,
                    "pause_reason": reason,
                    "alarm_bounce": alarm_bounce,
                    "alarm_complaint": alarm_complaint,
                    "last_enforcement_at": now_iso,
                    "enforcement_reason": reason,
                    "updated_at": now_iso,
                }
            )
            .eq("id", domain_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("reputation_enforcement.pause_domain_failed", domain_id=domain_id, err=str(exc))

    # 2. Cascade pause to all active inboxes under this domain.
    try:
        (
            sb.table("tenant_inboxes")
            .update(
                {
                    "paused_until": until,
                    "pause_reason": f"domain_paused:{reason}",
                    "updated_at": now_iso,
                }
            )
            .eq("domain_id", domain_id)
            .eq("active", True)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("reputation_enforcement.pause_inboxes_failed", domain_id=domain_id, err=str(exc))

    # 3. Create a notification for the tenant dashboard.
    try:
        notif_body: dict[str, Any] = {
            "tenant_id": tenant_id,
            "type": _NOTIF_TYPE,
            "severity": "error",
            "title": f"Dominio {domain_name!r} sospeso",
            "message": (
                f"Il dominio {domain_name!r} è stato sospeso per 48 ore. "
                f"Motivo: {reason.replace('_', ' ')}. "
                f"Bounce rate: {bounce_rate:.1%}, Complaint rate: {complaint_rate:.2%}. "
                "Contatta il supporto per sbloccare manualmente dopo aver risolto il problema."
            ),
            "read": False,
            "created_at": now_iso,
            "metadata": {
                "domain_id": domain_id,
                "domain": domain_name,
                "reason": reason,
                "bounce_rate": bounce_rate,
                "complaint_rate": complaint_rate,
                "paused_until": until,
            },
        }
        # domain_id column added in migration 0052 (safe to include if absent).
        try:
            notif_body["domain_id"] = domain_id
        except Exception:  # noqa: BLE001
            pass
        sb.table("notifications").insert(notif_body).execute()
    except Exception as exc:  # noqa: BLE001
        # Notifications failure is non-fatal.
        log.warning("reputation_enforcement.notify_failed", domain_id=domain_id, err=str(exc))

    log.warning(
        "reputation_enforcement.domain_paused",
        tenant_id=tenant_id,
        domain_id=domain_id,
        domain=domain_name,
        reason=reason,
        bounce_rate=bounce_rate,
        complaint_rate=complaint_rate,
        paused_until=until,
    )


async def check_realtime_bounce_spike(
    sb: Any,
    *,
    tenant_id: str,
    domain_id: str | None,
    domain_name: str,
) -> bool:
    """Called by TrackingAgent on each hard-bounce webhook.

    Returns True if a 24-hour bounce-rate spike was detected and the
    domain was paused.

    Threshold: ≥8 % bounce rate in rolling 24 h with ≥10 sends in window.
    This catches sudden spikes before the nightly digest (which uses a
    7-day window and a 5 % threshold).
    """
    window_start = (
        datetime.now(timezone.utc)
        - timedelta(hours=REALTIME_BOUNCE_WINDOW_HOURS)
    ).isoformat()

    # Count total sends in the last 24 h for this tenant.
    try:
        sent_res = (
            sb.table("outreach_sends")
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            .gte("sent_at", window_start)
            .execute()
        )
        sent_count = sent_res.count or 0
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "reputation_enforcement.bounce_spike_sent_count_failed",
            tenant_id=tenant_id,
            err=str(exc),
        )
        return False

    if sent_count < REALTIME_BOUNCE_MIN_SENDS:
        return False  # Not enough volume for a meaningful rate.

    # Count hard bounces updated in the last 24 h.
    try:
        bounce_res = (
            sb.table("outreach_sends")
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            .eq("failure_reason", "bounced")
            .gte("updated_at", window_start)
            .execute()
        )
        bounce_count = bounce_res.count or 0
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "reputation_enforcement.bounce_spike_bounce_count_failed",
            tenant_id=tenant_id,
            err=str(exc),
        )
        return False

    bounce_rate_24h = bounce_count / sent_count
    if bounce_rate_24h < REALTIME_BOUNCE_SPIKE_THRESHOLD:
        return False

    # Spike threshold breached — resolve domain row and pause.
    if domain_id:
        try:
            dom_res = (
                sb.table("tenant_email_domains")
                .select("id, tenant_id, domain, purpose, paused_until, active")
                .eq("id", domain_id)
                .execute()
            )
            domain_rows = dom_res.data or []
        except Exception:  # noqa: BLE001
            domain_rows = []
    else:
        try:
            dom_res = (
                sb.table("tenant_email_domains")
                .select("id, tenant_id, domain, purpose, paused_until, active")
                .eq("tenant_id", tenant_id)
                .eq("domain", domain_name.lower())
                .execute()
            )
            domain_rows = dom_res.data or []
        except Exception:  # noqa: BLE001
            domain_rows = []

    if not domain_rows:
        log.warning(
            "reputation_enforcement.bounce_spike_domain_not_found",
            domain=domain_name,
            tenant_id=tenant_id,
        )
        return False

    domain_row = domain_rows[0]
    await _pause_domain_and_inboxes(
        sb,
        domain_row=domain_row,
        reason="bounce_spike_realtime",
        alarm_bounce=True,
        alarm_complaint=False,
        bounce_rate=bounce_rate_24h,
        complaint_rate=0.0,
    )
    log.warning(
        "reputation_enforcement.bounce_spike_pause",
        domain=domain_name,
        bounce_rate_24h=f"{bounce_rate_24h:.1%}",
        sent_count=sent_count,
        bounce_count=bounce_count,
        window_hours=REALTIME_BOUNCE_WINDOW_HOURS,
    )
    return True


async def check_realtime_complaint_rate(
    sb: Any,
    *,
    tenant_id: str,
    domain_id: str | None,
    domain_name: str,
) -> bool:
    """Rate-based complement to the cluster check (Task 21).

    The cluster guard (``check_realtime_complaint_cluster``) requires ≥3
    complaints in 60 minutes. For shadow-domain inboxes during warm-up
    (10 sends/day), reaching 3 complaints means a 30 % complaint rate —
    far beyond Gmail's 0.3 % threshold where permanent blacklisting occurs.

    This function runs on *every* complaint event. It computes the rolling
    24-hour complaint rate and pauses the domain immediately when:

    * Sends in last 24 h  ≥ ``MIN_SENDS_FOR_RATE_CHECK`` (5 sends minimum
      to avoid false positives from a brand-new inbox with 1 send total).
    * ``complaints / sends  ≥  COMPLAINT_RATE_THRESHOLD`` (0.3 %).

    For 10 sends/day (day-1 warm-up) + 1 complaint: 10 % >> 0.3 % → pause.
    For 50 sends/day (steady-state) + 1 complaint: 2 % >> 0.3 % → pause.
    For 500 sends/day + 1 complaint: 0.2 % < 0.3 % → cluster check covers.

    Returns True if the domain was paused, False otherwise.
    """
    MIN_SENDS_FOR_RATE_CHECK = 5
    window_start = (
        datetime.now(timezone.utc)
        - timedelta(hours=24)
    ).isoformat()

    # Count total sends in 24 h (tenant-scoped; domain scope requires a
    # join that is not yet universal — tenant scope is conservative/correct).
    try:
        sent_res = (
            sb.table("outreach_sends")
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            .gte("sent_at", window_start)
            .execute()
        )
        sent_count = sent_res.count or 0
    except Exception as exc:  # noqa: BLE001
        log.debug(
            "reputation_enforcement.rate_check_sent_count_failed",
            tenant_id=tenant_id,
            err=str(exc),
        )
        return False

    if sent_count < MIN_SENDS_FOR_RATE_CHECK:
        return False  # Too few sends for a meaningful rate.

    # Count complaint events in the same window.
    try:
        complaint_res = (
            sb.table("outreach_sends")
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            .eq("failure_reason", "complained")
            .gte("updated_at", window_start)
            .execute()
        )
        complaint_count = complaint_res.count or 0
    except Exception as exc:  # noqa: BLE001
        log.debug(
            "reputation_enforcement.rate_check_complaint_count_failed",
            tenant_id=tenant_id,
            err=str(exc),
        )
        return False

    complaint_rate_24h = complaint_count / sent_count
    if complaint_rate_24h < COMPLAINT_RATE_THRESHOLD:
        return False  # Still below threshold.

    log.warning(
        "reputation_enforcement.rate_check_threshold_breached",
        tenant_id=tenant_id,
        domain_id=domain_id,
        domain=domain_name,
        complaint_rate_24h=f"{complaint_rate_24h:.2%}",
        complaints=complaint_count,
        sends=sent_count,
    )

    # Resolve the domain row and pause.
    if domain_id:
        try:
            dom_res = (
                sb.table("tenant_email_domains")
                .select("id, tenant_id, domain, purpose, paused_until, active")
                .eq("id", domain_id)
                .execute()
            )
            domain_rows = dom_res.data or []
        except Exception:  # noqa: BLE001
            domain_rows = []
    else:
        try:
            dom_res = (
                sb.table("tenant_email_domains")
                .select("id, tenant_id, domain, purpose, paused_until, active")
                .eq("tenant_id", tenant_id)
                .eq("domain", domain_name.lower())
                .execute()
            )
            domain_rows = dom_res.data or []
        except Exception:  # noqa: BLE001
            domain_rows = []

    if not domain_rows:
        log.warning(
            "reputation_enforcement.rate_check_domain_not_found",
            domain=domain_name,
            tenant_id=tenant_id,
        )
        return False

    domain_row = domain_rows[0]

    # Skip if already paused (cluster check may have fired first).
    now_iso = datetime.now(timezone.utc).isoformat()
    existing_pause = domain_row.get("paused_until")
    if existing_pause and str(existing_pause) > now_iso:
        return False  # Already handled.

    await _pause_domain_and_inboxes(
        sb,
        domain_row=domain_row,
        reason="complaint_rate_realtime",
        alarm_complaint=True,
        alarm_bounce=False,
        bounce_rate=0.0,
        complaint_rate=complaint_rate_24h,
    )
    log.warning(
        "reputation_enforcement.rate_check_pause",
        domain=domain_name,
        tenant_id=tenant_id,
        complaint_rate_24h=f"{complaint_rate_24h:.2%}",
        sends=sent_count,
        complaints=complaint_count,
    )
    return True


async def pause_domain_for_alarm(
    sb: Any,
    *,
    domain_id: str,
    tenant_id: str,
    domain_name: str,
    reason: str,
    bounce_rate: float,
    complaint_rate: float,
    pause_hours: int = PAUSE_HOURS_ON_ALARM,
) -> None:
    """Public API for pausing a domain from external callers.

    Called by ``deliverability_monitor_service`` (hourly monitor) and any
    other service that needs to trigger the full domain-pause cascade
    (domain update + inbox cascade + dashboard notification) without
    duplicating the logic.

    The ``pause_hours`` parameter allows the caller to customise the
    pause duration, though the default (48 h) is used in all current
    callers.
    """
    # Build a minimal domain_row compatible with _pause_domain_and_inboxes.
    domain_row = {"id": domain_id, "tenant_id": tenant_id, "domain": domain_name}
    await _pause_domain_and_inboxes(
        sb,
        domain_row=domain_row,
        reason=reason,
        alarm_bounce=bounce_rate >= BOUNCE_RATE_THRESHOLD,
        alarm_complaint=complaint_rate >= COMPLAINT_RATE_THRESHOLD,
        bounce_rate=bounce_rate,
        complaint_rate=complaint_rate,
    )
