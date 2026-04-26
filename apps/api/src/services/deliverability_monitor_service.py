"""Task 15 — Hourly deliverability monitor.

Runs every hour via ``workers.cron.deliverability_hourly_cron``.
Complements the nightly ``reputation_enforcement_service.run_enforcement()``
(which operates on a 7-day aggregate) with a rolling short-window check
so domain problems are caught **within the same business hour** rather
than the next morning.

What it does
------------
For every active (non-paused) email domain with recent outreach activity:
1. Count ``outreach_sends`` in the last ``WINDOW_HOURS`` hours (default 4 h).
2. Count ``events`` of type "bounced" / "complained" in the same window.
3. Compute rolling bounce rate and complaint rate.
4. If rate exceeds threshold AND minimum-volume guard is met:
   → call ``reputation_enforcement_service._pause_domain_for_alarm()``
      (the same pause logic used by the nightly pass) so the pause cascade
      (inboxes, notifications) is applied consistently.

Thresholds
----------
| Metric          | Threshold | Min sends | Pause hours |
|-----------------|-----------|-----------|-------------|
| Bounce rate     |   5 %     |    5      |    48 h     |
| Complaint rate  |   0.08 %  |    5      |    48 h     |
  (complaint threshold is tighter than the nightly 0.3% because Gmail
   reacts fast — one complaint in 10 sends is worth an early warning)

Relationship to reputation_enforcement_service
----------------------------------------------
* This service owns the *hourly* query + threshold logic.
* ``_pause_domain_for_alarm`` from enforcement_service does the actual DB
  writes + notification so the pause format is always consistent.
* The nightly ``reputation_digest_cron`` + ``run_enforcement()`` continues
  to run at 02:30 UTC as the definitive 7-day pass; this service is a
  *complementary early-warning*, not a replacement.

Non-critical: any error per domain is logged and skipped.  A transient
Supabase outage does NOT block the send pipeline.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Thresholds — kept in sync with reputation_enforcement_service.py
# ---------------------------------------------------------------------------

WINDOW_HOURS: int = 4              # rolling window for hourly check
MIN_SENDS_FOR_RATE: int = 5        # minimum sends to compute a meaningful rate

HOURLY_BOUNCE_THRESHOLD: float = 0.05    # 5 %  (same as nightly)
HOURLY_COMPLAINT_THRESHOLD: float = 0.001  # 0.1 % (tighter than nightly 0.3%)

PAUSE_HOURS: int = 48


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class MonitorResult:
    """Summary of one hourly monitor run."""

    domains_checked: int = 0
    domains_paused: int = 0
    domains_skipped_low_volume: int = 0
    errors: list[str] = field(default_factory=list)
    paused_details: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_hourly_monitor(sb: Any) -> MonitorResult:
    """Scan all active domains for short-window bounce / complaint spikes.

    Args:
        sb: Supabase service-role client (sync, wrapped in asyncio.to_thread).
    """
    from .reputation_enforcement_service import pause_domain_for_alarm

    result = MonitorResult()
    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(hours=WINDOW_HOURS)).isoformat()

    # 1. Load all non-paused tenant_email_domains that had recent sends.
    try:
        domains_res = await asyncio.to_thread(
            lambda: sb.table("tenant_email_domains")
            .select("id, tenant_id, domain, paused_until")
            .eq("purpose", "outreach")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.error("deliverability_monitor.domain_load_failed", err=str(exc))
        result.errors.append(f"domain_load: {exc}")
        return result

    domains = [
        row for row in (domains_res.data or [])
        if _is_active(row, now)
    ]

    if not domains:
        log.debug("deliverability_monitor.no_active_domains")
        return result

    result.domains_checked = len(domains)

    for domain_row in domains:
        domain_id: str = domain_row["id"]
        tenant_id: str = domain_row["tenant_id"]
        domain_name: str = domain_row["domain"]
        try:
            stats = await _compute_domain_stats(sb, domain_id, window_start)

            if stats["total_sends"] < MIN_SENDS_FOR_RATE:
                result.domains_skipped_low_volume += 1
                log.debug(
                    "deliverability_monitor.low_volume_skip",
                    domain=domain_name,
                    sends=stats["total_sends"],
                )
                continue

            bounce_rate = stats["bounces"] / stats["total_sends"]
            complaint_rate = stats["complaints"] / stats["total_sends"]

            should_pause = False
            pause_reason = ""

            if bounce_rate >= HOURLY_BOUNCE_THRESHOLD:
                should_pause = True
                pause_reason = "hourly_bounce_rate_exceeded"
            elif complaint_rate >= HOURLY_COMPLAINT_THRESHOLD:
                should_pause = True
                pause_reason = "hourly_complaint_rate_exceeded"

            if not should_pause:
                log.debug(
                    "deliverability_monitor.domain_ok",
                    domain=domain_name,
                    bounce_rate=f"{bounce_rate:.1%}",
                    complaint_rate=f"{complaint_rate:.2%}",
                    sends=stats["total_sends"],
                )
                continue

            # Pause the domain.
            await pause_domain_for_alarm(
                sb,
                domain_id=domain_id,
                tenant_id=tenant_id,
                domain_name=domain_name,
                reason=pause_reason,
                bounce_rate=bounce_rate,
                complaint_rate=complaint_rate,
                pause_hours=PAUSE_HOURS,
            )
            result.domains_paused += 1
            result.paused_details.append(
                {
                    "domain": domain_name,
                    "reason": pause_reason,
                    "bounce_rate": bounce_rate,
                    "complaint_rate": complaint_rate,
                    "sends_in_window": stats["total_sends"],
                }
            )
            log.warning(
                "deliverability_monitor.domain_paused",
                domain=domain_name,
                reason=pause_reason,
                bounce_rate=f"{bounce_rate:.1%}",
                complaint_rate=f"{complaint_rate:.3%}",
                window_hours=WINDOW_HOURS,
                sends=stats["total_sends"],
            )

        except Exception as exc:  # noqa: BLE001
            log.error(
                "deliverability_monitor.domain_error",
                domain=domain_name,
                err=str(exc),
            )
            result.errors.append(f"{domain_name}: {exc}")

    log.info(
        "deliverability_monitor.done",
        checked=result.domains_checked,
        paused=result.domains_paused,
        low_volume=result.domains_skipped_low_volume,
        errors=len(result.errors),
        window_hours=WINDOW_HOURS,
    )
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_active(domain_row: dict[str, Any], now: datetime) -> bool:
    """Return True when the domain is not currently paused."""
    paused_until = domain_row.get("paused_until")
    if not paused_until:
        return True
    try:
        paused_dt = datetime.fromisoformat(str(paused_until).replace("Z", "+00:00"))
        return paused_dt <= now
    except (ValueError, TypeError):
        return True


async def _compute_domain_stats(
    sb: Any,
    domain_id: str,
    window_start: str,
) -> dict[str, int]:
    """Return {total_sends, bounces, complaints} for domain in window.

    We join outreach_sends → tenant_inboxes → tenant_email_domains to
    scope sends to a specific domain.  Events join on outreach_sends.id
    via the email_message_id column (Resend's message ID).
    """
    # Count sends from inboxes on this domain in the window.
    # outreach_sends has no domain_id column directly — we go through
    # tenant_inboxes via the inbox_id column (set at send time).
    try:
        sends_res = await asyncio.to_thread(
            lambda: sb.table("outreach_sends")
            .select("id", count="exact")
            .gte("sent_at", window_start)
            .eq("inbox_domain_id", domain_id)   # denorm column added below
            .execute()
        )
        # Fallback: if inbox_domain_id column doesn't exist yet, use a
        # join-based sub-query via a function call.  The graceful fallback
        # means this works even before migration 0062 is applied.
        total_sends = sends_res.count or 0
    except Exception:  # noqa: BLE001
        # Column may not exist yet — fall back to in-memory join approach.
        total_sends, bounces, complaints = await _compute_domain_stats_fallback(
            sb, domain_id, window_start
        )
        return {"total_sends": total_sends, "bounces": bounces, "complaints": complaints}

    # Count bounces and complaints from events in window.
    try:
        events_res = await asyncio.to_thread(
            lambda: sb.rpc(
                "count_domain_events_in_window",
                {
                    "p_domain_id": domain_id,
                    "p_window_start": window_start,
                    "p_event_types": ["bounced", "complained"],
                },
            ).execute()
        )
        counts = {row["event_type"]: row["count"] for row in (events_res.data or [])}
        bounces = int(counts.get("bounced", 0))
        complaints = int(counts.get("complained", 0))
    except Exception:  # noqa: BLE001
        bounces, complaints = await _count_events_fallback(sb, domain_id, window_start)

    return {"total_sends": total_sends, "bounces": bounces, "complaints": complaints}


async def _compute_domain_stats_fallback(
    sb: Any,
    domain_id: str,
    window_start: str,
) -> tuple[int, int, int]:
    """Pure-Python fallback when denorm columns / RPC are not yet available.

    Fetches inbox IDs for the domain, then counts sends + events in Python.
    Slightly slower but safe as a bootstrap path.
    """
    # Inbox IDs on this domain.
    try:
        inboxes_res = await asyncio.to_thread(
            lambda: sb.table("tenant_inboxes")
            .select("id")
            .eq("domain_id", domain_id)
            .eq("active", True)
            .execute()
        )
        inbox_ids = [r["id"] for r in (inboxes_res.data or [])]
    except Exception:  # noqa: BLE001
        return 0, 0, 0

    if not inbox_ids:
        return 0, 0, 0

    # Sends
    try:
        sends_res = await asyncio.to_thread(
            lambda: sb.table("outreach_sends")
            .select("id, email_message_id")
            .in_("inbox_id", inbox_ids)
            .gte("sent_at", window_start)
            .limit(2000)   # safety cap — if a domain is sending >2000/4h we have bigger problems
            .execute()
        )
    except Exception:  # noqa: BLE001
        return 0, 0, 0

    send_rows = sends_res.data or []
    total_sends = len(send_rows)
    if not total_sends:
        return 0, 0, 0

    email_ids = [r["email_message_id"] for r in send_rows if r.get("email_message_id")]
    if not email_ids:
        return total_sends, 0, 0

    # Events for those sends
    try:
        events_res = await asyncio.to_thread(
            lambda: sb.table("events")
            .select("event_type")
            .in_("email_message_id", email_ids[:1000])  # Supabase .in_() has a limit
            .in_("event_type", ["bounced", "complained"])
            .gte("occurred_at", window_start)
            .execute()
        )
        event_rows = events_res.data or []
    except Exception:  # noqa: BLE001
        return total_sends, 0, 0

    bounces = sum(1 for e in event_rows if e["event_type"] == "bounced")
    complaints = sum(1 for e in event_rows if e["event_type"] == "complained")
    return total_sends, bounces, complaints


async def _count_events_fallback(
    sb: Any,
    domain_id: str,
    window_start: str,
) -> tuple[int, int]:
    """Fallback: count events without the RPC function."""
    _total, bounces, complaints = await _compute_domain_stats_fallback(
        sb, domain_id, window_start
    )
    return bounces, complaints
