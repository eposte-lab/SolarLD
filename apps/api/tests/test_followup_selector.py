"""Pure-function tests for ``services.followup_service.select_next_step``.

All timing knobs are injected via the ``now`` parameter so the
scheduler logic is trivially unit-testable without ``freezegun``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.services.followup_service import (
    CampaignSummary,
    FollowUpCandidate,
    STEP_2_DELAY_DAYS,
    STEP_3_DELAY_DAYS,
    build_candidate_from_rows,
    select_next_step,
)

D0 = datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc)


def _now(days_after: float) -> datetime:
    return D0 + timedelta(days=days_after)


def _candidate(
    *,
    status: str = "sent",
    channel: str | None = "email",
    day0_sent: datetime | None = D0,
    campaigns: tuple[CampaignSummary, ...] = (),
) -> FollowUpCandidate:
    return FollowUpCandidate(
        lead_id="lead-1",
        tenant_id="tenant-1",
        pipeline_status=status,
        outreach_channel=channel,
        outreach_sent_at=day0_sent,
        campaigns=campaigns,
    )


def _step1(status: str = "sent", sent_at: datetime = D0) -> CampaignSummary:
    return CampaignSummary(
        sequence_step=1, status=status, sent_at=sent_at, channel="email"
    )


def _step2(status: str = "sent", sent_at: datetime | None = None) -> CampaignSummary:
    return CampaignSummary(
        sequence_step=2,
        status=status,
        sent_at=sent_at,
        channel="email",
    )


# ---------------------------------------------------------------------------
# Channel + anchor gates
# ---------------------------------------------------------------------------


def test_skip_non_email_channel() -> None:
    c = _candidate(channel="postal")
    out = select_next_step(c, now=_now(10))
    assert out.should_send is False
    assert out.reason == "channel_not_email"


def test_skip_missing_day0_anchor() -> None:
    c = _candidate(day0_sent=None)
    out = select_next_step(c, now=_now(10))
    assert out.should_send is False
    assert out.reason == "no_initial_send"


@pytest.mark.parametrize(
    "bad_status",
    ["opened", "clicked", "engaged", "whatsapp", "appointment", "blacklisted"],
)
def test_skip_when_lead_engaged_or_terminal(bad_status: str) -> None:
    c = _candidate(status=bad_status)
    out = select_next_step(c, now=_now(10))
    assert out.should_send is False
    assert out.reason == "lead_engaged_or_terminal"


def test_skip_status_new_is_ineligible() -> None:
    # A lead without any outreach shouldn't be a follow-up candidate anyway.
    c = _candidate(status="new")
    out = select_next_step(c, now=_now(10))
    assert out.should_send is False
    assert out.reason == "status_ineligible"


def test_skip_when_step1_missing_in_campaigns() -> None:
    # Lead says 'sent' but no campaigns row to back it (data drift).
    c = _candidate(status="sent", campaigns=())
    out = select_next_step(c, now=_now(10))
    assert out.should_send is False
    assert out.reason == "no_step1_campaign"


def test_skip_when_step1_failed() -> None:
    c = _candidate(
        status="sent",
        campaigns=(_step1(status="failed"),),
    )
    out = select_next_step(c, now=_now(10))
    assert out.should_send is False
    assert out.reason == "step1_not_delivered"


# ---------------------------------------------------------------------------
# Step-2 decisions
# ---------------------------------------------------------------------------


def test_step2_too_early_before_cadence() -> None:
    c = _candidate(campaigns=(_step1(),))
    out = select_next_step(c, now=_now(STEP_2_DELAY_DAYS - 1))
    assert out.should_send is False
    assert out.reason is not None and out.reason.startswith("too_early_for_step2")


def test_step2_fires_at_cadence() -> None:
    c = _candidate(campaigns=(_step1(),))
    out = select_next_step(c, now=_now(STEP_2_DELAY_DAYS))
    assert out.should_send is True
    assert out.step == 2


def test_step2_skips_when_already_sent() -> None:
    c = _candidate(
        campaigns=(_step1(), _step2(sent_at=_now(STEP_2_DELAY_DAYS))),
    )
    # Step 2 already queued — no duplicate.
    out = select_next_step(c, now=_now(STEP_2_DELAY_DAYS + 0.5))
    assert out.should_send is False


def test_step2_delivered_status_still_silent_enough() -> None:
    # A lead that was 'delivered' by Resend webhooks but never opened is
    # still eligible — delivered is a silent state.
    c = _candidate(status="delivered", campaigns=(_step1(status="delivered"),))
    out = select_next_step(c, now=_now(STEP_2_DELAY_DAYS + 1))
    assert out.should_send is True
    assert out.step == 2


# ---------------------------------------------------------------------------
# Step-3 decisions
# ---------------------------------------------------------------------------


def test_step3_too_early_skips() -> None:
    c = _candidate(
        campaigns=(_step1(), _step2(sent_at=_now(STEP_2_DELAY_DAYS))),
    )
    out = select_next_step(c, now=_now(STEP_3_DELAY_DAYS - 2))
    assert out.should_send is False
    assert out.reason is not None and out.reason.startswith("too_early_for_step3")


def test_step3_fires_at_cadence() -> None:
    c = _candidate(
        campaigns=(_step1(), _step2(sent_at=_now(STEP_2_DELAY_DAYS))),
    )
    out = select_next_step(c, now=_now(STEP_3_DELAY_DAYS))
    assert out.should_send is True
    assert out.step == 3


def test_step3_respects_min_gap_between_steps() -> None:
    # Step 2 was sent only yesterday (maybe day-0 was backfilled). Even
    # if the day-0 anchor is ancient, we still respect the min gap.
    recent_step2 = _step2(sent_at=_now(STEP_3_DELAY_DAYS - 1))
    c = _candidate(campaigns=(_step1(), recent_step2))
    out = select_next_step(c, now=_now(STEP_3_DELAY_DAYS))
    assert out.should_send is False
    assert out.reason is not None and "step2_too_recent" in out.reason


def test_step3_skips_when_already_sent() -> None:
    step3 = CampaignSummary(
        sequence_step=3,
        status="sent",
        sent_at=_now(STEP_3_DELAY_DAYS),
        channel="email",
    )
    c = _candidate(
        campaigns=(
            _step1(),
            _step2(sent_at=_now(STEP_2_DELAY_DAYS)),
            step3,
        ),
    )
    out = select_next_step(c, now=_now(STEP_3_DELAY_DAYS + 5))
    assert out.should_send is False
    assert out.reason == "sequence_complete"


def test_step3_skips_when_step2_pending() -> None:
    # Step 2 row exists but status=pending (arq queued it but Resend
    # didn't accept yet). Don't escalate to step 3 prematurely.
    step2_pending = _step2(status="pending", sent_at=None)
    c = _candidate(campaigns=(_step1(), step2_pending))
    out = select_next_step(c, now=_now(STEP_3_DELAY_DAYS + 5))
    assert out.should_send is False
    assert out.reason == "step2_not_delivered"


def test_step3_skips_when_step2_failed() -> None:
    c = _candidate(
        campaigns=(
            _step1(),
            _step2(status="failed", sent_at=_now(STEP_2_DELAY_DAYS)),
        ),
    )
    out = select_next_step(c, now=_now(STEP_3_DELAY_DAYS + 1))
    assert out.should_send is False
    assert out.reason == "step2_not_delivered"


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


def test_build_candidate_from_rows_handles_iso_timestamps() -> None:
    lead = {
        "id": "L",
        "tenant_id": "T",
        "pipeline_status": "sent",
        "outreach_channel": "email",
        "outreach_sent_at": "2026-04-01T09:00:00Z",
    }
    campaigns = [
        {
            "sequence_step": 1,
            "status": "sent",
            "sent_at": "2026-04-01T09:00:00+00:00",
            "channel": "email",
        },
    ]
    cand = build_candidate_from_rows(lead=lead, campaigns=campaigns)
    assert cand.outreach_sent_at == D0
    assert cand.campaigns[0].sent_at == D0
    assert cand.outreach_channel == "email"


def test_build_candidate_from_rows_tolerates_nulls() -> None:
    lead = {
        "id": "L",
        "tenant_id": "T",
        "pipeline_status": "sent",
        "outreach_channel": None,
        "outreach_sent_at": None,
    }
    cand = build_candidate_from_rows(lead=lead, campaigns=[])
    assert cand.outreach_sent_at is None
    assert cand.outreach_channel is None
    assert cand.campaigns == ()
