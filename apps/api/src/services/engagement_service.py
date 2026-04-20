"""Engagement scoring & nightly rollup (Part B.1 deep-tracking).

The score is the dashboard's "heat" metric — it tells the installer
which leads are worth calling *today*. It lives as a denormalised
integer 0-100 on ``leads.engagement_score`` and is refreshed nightly
by ``engagement_rollup_cron``. Real-time "is this lead active right
now?" is computed on demand in the dashboard data fetcher from the
last N minutes of ``portal_events`` — we don't trigger-update the
score on every heartbeat because it'd cause write amplification on
``leads`` (dozens of updates per portal visit).

Formula (v1 — tunable, keep in sync with UI copy in /settings):

    +20   per distinct portal session in the last 30 days
    + 3   per portal.scroll_50 event
    + 7   per portal.scroll_90 event
    +10   per portal.roi_viewed event
    + 2   per portal.cta_hover event (capped at 10 total)
    +15   per portal.video_play event
    +25   per portal.video_complete event
    +40   per portal.whatsapp_click
    +60   per portal.appointment_click
    + 1   per 30s of time-on-page (capped at 20)
    + 5   if outreach_opened_at is set         (email open, lifetime)
    +15   if outreach_clicked_at is set        (email click, lifetime)

Clamped to [0, 100]. Inputs outside the 30-day window are dropped to
keep the score sensitive — a lead who was hot in January shouldn't
dominate the April "hot leads" list if they've since gone cold.

Rollup side-effects:
  * Sets ``engagement_score``, ``engagement_score_updated_at``,
    ``portal_sessions``, ``portal_total_time_sec``,
    ``deepest_scroll_pct`` on every lead that has at least one
    portal event in the window.
  * Leaves leads with zero portal activity untouched (their score
    stays at its previous value until they earn a new event). This
    avoids writing rows we have no new signal for.

Dashboard real-time companion (NOT in this file):
    ``get_hot_leads_now(tenant_id, minutes=60)`` reads
    ``portal_events`` directly for the last hour — see the dashboard
    side at ``apps/dashboard/src/lib/data/engagement.ts``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

log = get_logger(__name__)

# Rolling window — 30 days matches the "caldi di oggi" use case: a
# lead active in the last month is a plausible sales target.
ROLLUP_WINDOW_DAYS = 30

# How often portal.heartbeat fires from the client (migration-level
# contract with apps/lead-portal/src/lib/tracking.ts). Used to convert
# heartbeat count → time-on-page seconds.
HEARTBEAT_INTERVAL_SEC = 15

# Weights — see module docstring. Change these in lockstep with the
# /settings page copy so what the operator sees matches what they get.
W_SESSION = 20
W_SCROLL_50 = 3
W_SCROLL_90 = 7
W_ROI_VIEWED = 10
W_CTA_HOVER = 2
W_CTA_HOVER_CAP = 10
W_VIDEO_PLAY = 15
W_VIDEO_COMPLETE = 25
W_WHATSAPP_CLICK = 40
W_APPOINTMENT_CLICK = 60
W_TIME_PER_30S = 1
W_TIME_CAP = 20
W_EMAIL_OPENED = 5
W_EMAIL_CLICKED = 15

SCORE_MAX = 100


@dataclass
class LeadEngagementStats:
    """Per-lead accumulator — shaped to match the nightly rollup SQL."""

    lead_id: str
    tenant_id: str

    sessions: set[str] = field(default_factory=set)
    scroll_50: int = 0
    scroll_90: int = 0
    roi_viewed: int = 0
    cta_hover: int = 0
    video_play: int = 0
    video_complete: int = 0
    whatsapp_click: int = 0
    appointment_click: int = 0
    heartbeats: int = 0
    deepest_scroll_pct: int = 0

    # Email-level signals from the leads row (not events). Populated
    # by the rollup after the loop.
    outreach_opened: bool = False
    outreach_clicked: bool = False

    @property
    def total_time_sec(self) -> int:
        return self.heartbeats * HEARTBEAT_INTERVAL_SEC


def compute_score(stats: LeadEngagementStats) -> int:
    """Pure function — stats in, 0..100 out.

    Split from the I/O-bound rollup so unit tests can feed a hand-
    crafted stats object and assert the exact boundary conditions
    (e.g. CTA-hover cap, time cap) without Supabase.
    """
    score = 0
    score += W_SESSION * len(stats.sessions)
    score += W_SCROLL_50 * stats.scroll_50
    score += W_SCROLL_90 * stats.scroll_90
    score += W_ROI_VIEWED * stats.roi_viewed
    score += min(W_CTA_HOVER * stats.cta_hover, W_CTA_HOVER_CAP)
    score += W_VIDEO_PLAY * stats.video_play
    score += W_VIDEO_COMPLETE * stats.video_complete
    score += W_WHATSAPP_CLICK * stats.whatsapp_click
    score += W_APPOINTMENT_CLICK * stats.appointment_click

    # Time-on-page in 30s buckets, capped.
    time_points = (stats.total_time_sec // 30) * W_TIME_PER_30S
    score += min(int(time_points), W_TIME_CAP)

    if stats.outreach_opened:
        score += W_EMAIL_OPENED
    if stats.outreach_clicked:
        score += W_EMAIL_CLICKED

    return max(0, min(SCORE_MAX, score))


async def run_engagement_rollup(
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Refresh ``leads.engagement_score`` for all active leads.

    Scans the last 30 days of ``portal_events`` + pulls the email
    engagement timestamps from ``leads`` and writes back the
    aggregated rollup columns in a single UPDATE-per-lead batch.

    Returns ``{"leads_updated": N, "scored_hot": M}`` for logging
    (``scored_hot`` counts leads whose new score is >=60, the UX
    "hot right now" threshold).
    """
    sb = get_service_client()
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(days=ROLLUP_WINDOW_DAYS)

    # ------------------------------------------------------------------
    # 1) Pull last-30d portal events — one pass, group in Python.
    # ------------------------------------------------------------------
    events_res = (
        sb.table("portal_events")
        .select(
            "tenant_id, lead_id, session_id, event_kind, metadata"
        )
        .gte("occurred_at", window_start.isoformat())
        .execute()
    )

    by_lead: dict[str, LeadEngagementStats] = {}
    for row in events_res.data or []:
        lid = row.get("lead_id")
        tid = row.get("tenant_id")
        if not lid or not tid:
            continue
        stats = by_lead.setdefault(
            lid, LeadEngagementStats(lead_id=lid, tenant_id=tid)
        )
        sid = row.get("session_id")
        if sid:
            stats.sessions.add(str(sid))

        kind = row.get("event_kind") or ""
        meta = row.get("metadata") or {}

        if kind == "portal.scroll_50":
            stats.scroll_50 += 1
            stats.deepest_scroll_pct = max(stats.deepest_scroll_pct, 50)
        elif kind == "portal.scroll_90":
            stats.scroll_90 += 1
            stats.deepest_scroll_pct = max(stats.deepest_scroll_pct, 90)
        elif kind == "portal.roi_viewed":
            stats.roi_viewed += 1
        elif kind == "portal.cta_hover":
            stats.cta_hover += 1
        elif kind == "portal.video_play":
            stats.video_play += 1
        elif kind == "portal.video_complete":
            stats.video_complete += 1
        elif kind == "portal.whatsapp_click":
            stats.whatsapp_click += 1
        elif kind == "portal.appointment_click":
            stats.appointment_click += 1
        elif kind == "portal.heartbeat":
            stats.heartbeats += 1
        # portal.view / portal.leave contribute only via session count
        # (view defines session start, leave carries final elapsed_ms
        # which we don't currently scoreboard).

        # Opportunistic scroll-pct capture from metadata (client may
        # send {pct: 75} on a custom milestone in future).
        pct = meta.get("pct")
        if isinstance(pct, (int, float)) and pct > stats.deepest_scroll_pct:
            stats.deepest_scroll_pct = int(min(100, max(0, pct)))

    if not by_lead:
        log.info("engagement.rollup.no_events")
        return {"leads_updated": 0, "scored_hot": 0}

    # ------------------------------------------------------------------
    # 2) Pull email engagement timestamps for those leads in one shot.
    # ------------------------------------------------------------------
    lead_ids = list(by_lead.keys())
    leads_res = (
        sb.table("leads")
        .select("id, outreach_opened_at, outreach_clicked_at")
        .in_("id", lead_ids)
        .execute()
    )
    for row in leads_res.data or []:
        lid = row.get("id")
        if lid not in by_lead:
            continue
        by_lead[lid].outreach_opened = bool(row.get("outreach_opened_at"))
        by_lead[lid].outreach_clicked = bool(row.get("outreach_clicked_at"))

    # ------------------------------------------------------------------
    # 3) Compute scores + write back. Supabase PostgREST doesn't
    # support a single UPDATE with per-row values, so we loop — but
    # the batch is bounded by "leads with activity in last 30 days"
    # which is inherently small (hundreds, not millions).
    # ------------------------------------------------------------------
    now_iso = now.isoformat()
    updated = 0
    hot = 0
    errors = 0
    for stats in by_lead.values():
        score = compute_score(stats)
        try:
            sb.table("leads").update(
                {
                    "engagement_score": score,
                    "engagement_score_updated_at": now_iso,
                    "portal_sessions": len(stats.sessions),
                    "portal_total_time_sec": stats.total_time_sec,
                    "deepest_scroll_pct": stats.deepest_scroll_pct,
                }
            ).eq("id", stats.lead_id).execute()
        except Exception as exc:  # noqa: BLE001
            errors += 1
            log.warning(
                "engagement.rollup.update_failed",
                lead_id=stats.lead_id,
                err=str(exc),
            )
            continue
        updated += 1
        if score >= 60:
            hot += 1

    log.info(
        "engagement.rollup.done",
        leads_updated=updated,
        scored_hot=hot,
        errors=errors,
        window_start=window_start.isoformat(),
    )
    return {
        "leads_updated": updated,
        "scored_hot": hot,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Real-time helper — NOT called by the cron; exposed for ad-hoc tests
# and potential future admin endpoints. The dashboard implements its
# own TypeScript version of this query in ``lib/data/engagement.ts``.
# ---------------------------------------------------------------------------


async def get_hot_leads_now(
    tenant_id: str,
    *,
    minutes: int = 60,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Leads with the most portal_events in the last ``minutes`` minutes.

    Useful for a watchdog script or CLI debugging; the dashboard reads
    the same signal from its own TypeScript fetcher so we don't bounce
    through the API for every page load.
    """
    sb = get_service_client()
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    res = (
        sb.table("portal_events")
        .select("lead_id")
        .eq("tenant_id", tenant_id)
        .gte("occurred_at", since)
        .execute()
    )
    counts: dict[str, int] = defaultdict(int)
    for row in res.data or []:
        lid = row.get("lead_id")
        if lid:
            counts[lid] += 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"lead_id": lid, "recent_events": n} for lid, n in ranked]
