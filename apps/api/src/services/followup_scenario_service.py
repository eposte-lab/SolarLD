"""Engagement-based follow-up scenario selector.

Pure decision module that picks one of six scenarios for a lead given
its current engagement state. The cron (``workers/cron.py``) calls
``evaluate_followup_scenario(snapshot, now=...)`` and, when a scenario
is returned, enqueues a tailored email (or, for the ``hot`` scenario,
just a notification — no email).

Score buckets (existing 0-100 scale, no remap):

    cold         score == 0  AND no engagement signal AND cold cadence done
    lukewarm     1 <= score <= 20
    engaged     21 <= score <= 40
    interessato 41 <= score <= 60
    hot         score >= 61            (no email, operator handoff)
    riattivazione  peak_score >= 40 AND now silent for 14d+

Cadence: each scenario has a minimum gap before re-firing on the same
lead. ``cold`` is gated on the legacy step-2/3/4 cadence having
completed (the breakup at d+14) — we don't want to double-touch a lead
that's still inside the cold sequence.

The "hot" path is special: we *never* send an email automatically when
score crosses 61. The lead is manually nurtured by the installer. We
only emit one notification (deduped via ``hot_lead_alerted_at``).

This module is intentionally pure and Supabase-free; all DB I/O lives
in ``workers/cron.py`` so this file stays unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

Scenario = Literal[
    "cold",
    "lukewarm",
    "engaged",
    "interessato",
    "hot",
    "riattivazione",
]

# ---------------------------------------------------------------------------
# Score thresholds (0-100 scale, inclusive lower bound)
# ---------------------------------------------------------------------------
SCORE_LUKEWARM_MIN = 1
SCORE_ENGAGED_MIN = 21
SCORE_INTERESSATO_MIN = 41
SCORE_HOT_MIN = 61

# Riattivazione: peak score crossed this threshold but lead has been
# silent (no portal/email event) for at least RIATTIVAZIONE_SILENT_DAYS.
RIATTIVAZIONE_PEAK_MIN = 40
RIATTIVAZIONE_SILENT_DAYS = 14

# Per-scenario cooldowns (days between consecutive fires of same scenario)
COOLDOWN_DAYS: dict[Scenario, int] = {
    "cold": 30,           # cold leads: rare touch, once a month
    "lukewarm": 14,       # 2-week cadence
    "engaged": 10,        # weekly-ish, sector news rotates
    "interessato": 7,     # tightest legitimate cadence
    "hot": 30,            # operator does manual outreach; alert at most monthly
    "riattivazione": 30,  # one-shot reactivation, then back to cold cadence
}

# After the cold sequence (step 4 d+14 breakup) we wait this long before
# starting the engagement-based follow-up scenarios. Gives the lead a
# cooling period.
POST_COLD_QUIET_DAYS = 21


@dataclass(slots=True, frozen=True)
class FollowupSnapshot:
    """Inputs the cron passes in for one lead. Pure."""

    lead_id: str
    tenant_id: str
    pipeline_status: str
    engagement_score: int  # 0-100
    engagement_peak_score: int  # 0-100, all-time max
    last_engagement_at: datetime | None  # last portal_event or open/click
    initial_outreach_at: datetime | None
    last_followup_scenario: str | None
    last_followup_sent_at: datetime | None
    hot_lead_alerted_at: datetime | None
    cold_sequence_complete: bool  # step 4 (d+14 breakup) sent or aged-out


@dataclass(slots=True, frozen=True)
class ScenarioDecision:
    should_act: bool
    scenario: Scenario | None = None
    reason: str | None = None
    # When True, the action is "notify operator", not "send email".
    notify_only: bool = False


def evaluate_followup_scenario(
    snap: FollowupSnapshot, *, now: datetime
) -> ScenarioDecision:
    """Return the next follow-up action for this lead, or ``no_action``.

    Decision tree (first match wins, top-down):

        1. HOT — score >= 61 → notification (dedup on hot_lead_alerted_at)
        2. INTERESSATO — score in 41..60
        3. ENGAGED — score in 21..40
        4. LUKEWARM — score in 1..20
        5. RIATTIVAZIONE — peak >= 40 AND silent >= 14d
        6. COLD — score == 0 AND cold sequence complete + 21d cooling

    Each path checks per-scenario cooldown before approving.
    """
    if snap.engagement_score < 0 or snap.engagement_score > 100:
        return ScenarioDecision(False, reason="invalid_score")

    # ---- Hot lead alert path (no email) -------------------------------
    if snap.engagement_score >= SCORE_HOT_MIN:
        if snap.hot_lead_alerted_at is None:
            return ScenarioDecision(True, scenario="hot", notify_only=True)
        # Alert was sent recently — skip until score drops & re-rises or
        # the configured cooldown elapses.
        cooldown = timedelta(days=COOLDOWN_DAYS["hot"])
        if _aware(snap.hot_lead_alerted_at) + cooldown <= now:
            return ScenarioDecision(True, scenario="hot", notify_only=True)
        return ScenarioDecision(False, reason="hot_already_alerted")

    # ---- Tiered engagement scenarios ----------------------------------
    if snap.engagement_score >= SCORE_INTERESSATO_MIN:
        return _gated(snap, "interessato", now)
    if snap.engagement_score >= SCORE_ENGAGED_MIN:
        return _gated(snap, "engaged", now)
    if snap.engagement_score >= SCORE_LUKEWARM_MIN:
        return _gated(snap, "lukewarm", now)

    # ---- Score == 0 from here -----------------------------------------

    # Riattivazione: was warm/hot, now flatlined for 14d+
    if (
        snap.engagement_peak_score >= RIATTIVAZIONE_PEAK_MIN
        and snap.last_engagement_at is not None
        and (now - _aware(snap.last_engagement_at)).days >= RIATTIVAZIONE_SILENT_DAYS
    ):
        return _gated(snap, "riattivazione", now)

    # Cold path — only if the legacy d+4/9/14 cadence is done and a
    # cooling window has passed.
    if not snap.cold_sequence_complete:
        return ScenarioDecision(False, reason="cold_sequence_in_progress")
    if snap.initial_outreach_at is None:
        return ScenarioDecision(False, reason="no_initial_outreach")
    cold_eligible_at = _aware(snap.initial_outreach_at) + timedelta(
        days=14 + POST_COLD_QUIET_DAYS
    )
    if cold_eligible_at > now:
        return ScenarioDecision(False, reason="cold_cooling_window")
    return _gated(snap, "cold", now)


def _gated(
    snap: FollowupSnapshot, scenario: Scenario, now: datetime
) -> ScenarioDecision:
    """Apply per-scenario cooldown."""
    cooldown_days = COOLDOWN_DAYS[scenario]
    last = snap.last_followup_sent_at
    if last is None:
        return ScenarioDecision(True, scenario=scenario)
    if (now - _aware(last)).days >= cooldown_days:
        return ScenarioDecision(True, scenario=scenario)
    return ScenarioDecision(
        False,
        scenario=scenario,
        reason=f"cooldown_{scenario}_{(now - _aware(last)).days}d",
    )


def _aware(ts: datetime) -> datetime:
    """Force-tz-aware (UTC) for safe arithmetic."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


# ---------------------------------------------------------------------------
# Scenario → human label (for UI / audit logs)
# ---------------------------------------------------------------------------
SCENARIO_LABELS: dict[Scenario, str] = {
    "cold": "Cold (re-anchor)",
    "lukewarm": "Tiepido",
    "engaged": "Coinvolto",
    "interessato": "Interessato",
    "hot": "Hot (operator handoff)",
    "riattivazione": "Riattivazione",
}
