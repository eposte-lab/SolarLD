"""Follow-up sequence rules — pure, fully unit-testable.

The cron (``workers/cron.py``) pulls candidate leads every morning and
calls ``select_next_step(lead, campaigns, now)`` to decide whether a
follow-up email should be enqueued for that lead, and which step.

Cadence (from PRD):

    step 1 — Day 0   (initial outreach, sent by OutreachAgent on demand)
    step 2 — Day 4   nudge — only if pipeline_status in {sent, delivered}
                              (i.e. no engagement yet)
    step 3 — Day 11  last chance — only if still sent/delivered
                              and step 2 has been sent ≥ 7 days ago

Safety rails:

  * Never fire a follow-up if the lead has already been OPENED,
    CLICKED, ENGAGED, BLACKLISTED, etc. Any upward movement past
    ``delivered`` halts the sequence — the tenant should take it from
    there through WhatsApp / a sales call.
  * Never double-fire: if a ``campaigns`` row already exists with
    ``(lead_id, sequence_step=N)`` we skip.
  * If the first outreach FAILED (no verified email, Resend 4xx) we
    never escalate to step 2/3 — there's no point nudging when the
    send didn't land.
  * Only EMAIL channel for now. Postal follow-up is out of scope.

All decisions are made off the ``FollowUpCandidate`` dataclass so the
cron can unit-test the selector without hitting Supabase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..models.enums import CampaignStatus, LeadStatus, OutreachChannel

# Cadence offsets from the day-0 outreach.
STEP_2_DELAY_DAYS = 4    # d+4: nudge
STEP_3_DELAY_DAYS = 9    # d+9: soft case study
STEP_4_DELAY_DAYS = 14   # d+14: breakup email ("chiudo il caso")
# Step N should never fire less than this after step N-1 (guard-rail in
# case the clock skews or the first send was batch-backfilled).
MIN_GAP_BETWEEN_STEPS_DAYS = 3

# Pipeline states that HALT the sequence — any engagement past
# ``delivered`` means the lead is no longer "cold silence".
ENGAGED_STATES = frozenset(
    {
        LeadStatus.OPENED.value,
        LeadStatus.CLICKED.value,
        LeadStatus.ENGAGED.value,
        LeadStatus.WHATSAPP.value,
        LeadStatus.APPOINTMENT.value,
        LeadStatus.CLOSED_WON.value,
        LeadStatus.CLOSED_LOST.value,
        LeadStatus.BLACKLISTED.value,
    }
)

# Pipeline states where a follow-up is still appropriate — the lead
# received the email but hasn't opened it yet.
SILENT_STATES = frozenset({LeadStatus.SENT.value, LeadStatus.DELIVERED.value})


@dataclass(slots=True, frozen=True)
class FollowUpCandidate:
    """Shape the cron passes into the selector.

    Kept narrow so the selector stays pure and easy to fuzz-test.
    """

    lead_id: str
    tenant_id: str
    pipeline_status: str
    outreach_channel: str | None
    outreach_sent_at: datetime | None  # day-0 initial send time
    # One entry per historical campaign row for this lead.
    campaigns: tuple["CampaignSummary", ...] = ()


@dataclass(slots=True, frozen=True)
class CampaignSummary:
    sequence_step: int
    status: str                            # pending|sent|delivered|failed|cancelled
    sent_at: datetime | None
    channel: str = OutreachChannel.EMAIL.value


@dataclass(slots=True, frozen=True)
class FollowUpDecision:
    """Result of ``select_next_step``.

    When ``should_send`` is False, ``reason`` explains why so the cron
    can log + report in a single event for the operator.
    """

    should_send: bool
    step: int | None = None            # 2 or 3
    reason: str | None = None


def select_next_step(
    candidate: FollowUpCandidate, *, now: datetime
) -> FollowUpDecision:
    """Decide whether to enqueue a follow-up email for this lead.

    ``now`` is injected so tests can freeze the clock deterministically.
    """
    # ---- Channel guard ------------------------------------------------
    if candidate.outreach_channel != OutreachChannel.EMAIL.value:
        return FollowUpDecision(False, reason="channel_not_email")

    # ---- Must have a day-0 send anchor --------------------------------
    if candidate.outreach_sent_at is None:
        return FollowUpDecision(False, reason="no_initial_send")

    # ---- Must still be in a silent state ------------------------------
    if candidate.pipeline_status in ENGAGED_STATES:
        return FollowUpDecision(False, reason="lead_engaged_or_terminal")
    if candidate.pipeline_status not in SILENT_STATES:
        return FollowUpDecision(False, reason="status_ineligible")

    # ---- Day-0 must have actually landed, not just been attempted -----
    first_send = _find_step(candidate.campaigns, step=1)
    if first_send is None:
        return FollowUpDecision(False, reason="no_step1_campaign")
    if first_send.status not in {
        CampaignStatus.SENT.value,
        CampaignStatus.DELIVERED.value,
    }:
        return FollowUpDecision(False, reason="step1_not_delivered")

    # ---- Dedupe on already-queued steps -------------------------------
    if _find_step(candidate.campaigns, step=2) is None:
        # Evaluate step 2
        age_days = _days_between(candidate.outreach_sent_at, now)
        if age_days >= STEP_2_DELAY_DAYS:
            return FollowUpDecision(True, step=2)
        return FollowUpDecision(
            False,
            reason=f"too_early_for_step2(age={age_days:.1f}d)",
        )

    # Step 2 already went — check step 3 eligibility
    step3_row = _find_step(candidate.campaigns, step=3)
    if step3_row is None:
        step2 = _find_step(candidate.campaigns, step=2)
        # Step 2 still pending (not yet sent) → wait, don't queue step 3.
        if step2 is None or step2.status not in {
            CampaignStatus.SENT.value,
            CampaignStatus.DELIVERED.value,
        }:
            return FollowUpDecision(False, reason="step2_not_delivered")

        age_days = _days_between(candidate.outreach_sent_at, now)
        gap_days = (
            _days_between(step2.sent_at, now)
            if step2.sent_at is not None
            else 0.0
        )
        if age_days >= STEP_3_DELAY_DAYS and gap_days >= MIN_GAP_BETWEEN_STEPS_DAYS:
            return FollowUpDecision(True, step=3)
        if age_days < STEP_3_DELAY_DAYS:
            return FollowUpDecision(
                False, reason=f"too_early_for_step3(age={age_days:.1f}d)"
            )
        return FollowUpDecision(
            False, reason=f"step2_too_recent(gap={gap_days:.1f}d)"
        )

    # Step 3 already went — check step 4 (breakup email at d+14).
    if _find_step(candidate.campaigns, step=4) is not None:
        return FollowUpDecision(False, reason="sequence_complete")

    # Step 3 still pending → wait.
    if step3_row.status not in {
        CampaignStatus.SENT.value,
        CampaignStatus.DELIVERED.value,
    }:
        return FollowUpDecision(False, reason="step3_not_delivered")

    age_days = _days_between(candidate.outreach_sent_at, now)
    gap_days = (
        _days_between(step3_row.sent_at, now)
        if step3_row.sent_at is not None
        else 0.0
    )
    if age_days >= STEP_4_DELAY_DAYS and gap_days >= MIN_GAP_BETWEEN_STEPS_DAYS:
        return FollowUpDecision(True, step=4)
    if age_days < STEP_4_DELAY_DAYS:
        return FollowUpDecision(
            False, reason=f"too_early_for_step4(age={age_days:.1f}d)"
        )
    return FollowUpDecision(
        False, reason=f"step3_too_recent(gap={gap_days:.1f}d)"
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _find_step(
    campaigns: tuple[CampaignSummary, ...], *, step: int
) -> CampaignSummary | None:
    """Return the most-recent email campaign matching this step."""
    matches = [
        c
        for c in campaigns
        if c.sequence_step == step and c.channel == OutreachChannel.EMAIL.value
    ]
    if not matches:
        return None
    # Prefer the latest by sent_at (falls back to list order if not set).
    matches.sort(key=lambda c: c.sent_at or datetime.min.replace(tzinfo=timezone.utc))
    return matches[-1]


def _days_between(earlier: datetime, later: datetime) -> float:
    """Floating-point day delta. Handles naive vs aware defensively."""
    if earlier.tzinfo is None:
        earlier = earlier.replace(tzinfo=timezone.utc)
    if later.tzinfo is None:
        later = later.replace(tzinfo=timezone.utc)
    delta: timedelta = later - earlier
    return delta.total_seconds() / 86_400.0


def build_candidate_from_rows(
    *,
    lead: dict[str, Any],
    campaigns: list[dict[str, Any]],
) -> FollowUpCandidate:
    """Adapter: Supabase rows → FollowUpCandidate. Pure (no DB)."""
    return FollowUpCandidate(
        lead_id=str(lead["id"]),
        tenant_id=str(lead["tenant_id"]),
        pipeline_status=str(lead.get("pipeline_status") or ""),
        outreach_channel=lead.get("outreach_channel"),
        outreach_sent_at=_parse_ts(lead.get("outreach_sent_at")),
        campaigns=tuple(
            CampaignSummary(
                sequence_step=int(c.get("sequence_step") or 0),
                status=str(c.get("status") or ""),
                sent_at=_parse_ts(c.get("sent_at")),
                channel=str(c.get("channel") or OutreachChannel.EMAIL.value),
            )
            for c in campaigns
        ),
    )


def _parse_ts(raw: Any) -> datetime | None:
    """Supabase returns ISO strings (sometimes with 'Z'), sometimes None."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    s = str(raw).strip()
    if not s:
        return None
    # Postgres emits "2026-04-12T10:00:00+00:00" or with "Z" suffix.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
