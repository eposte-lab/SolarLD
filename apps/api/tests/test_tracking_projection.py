"""Pure-function tests for the Tracking Agent's event projection.

The agent's main ``execute`` method is mostly side effects (DB reads +
updates). The two pure projectors ``project_resend_lead_update`` and
``project_resend_campaign_update`` carry all the business rules, so we
exercise those heavily here.
"""

from __future__ import annotations

import pytest

from src.agents.tracking import (
    project_pixart_campaign_update,
    project_pixart_lead_update,
    project_resend_campaign_update,
    project_resend_lead_update,
)
from src.models.enums import CampaignStatus, LeadStatus


# ---------------------------------------------------------------------------
# Lead-row projection
# ---------------------------------------------------------------------------


def test_delivered_sets_timestamp_and_status() -> None:
    upd = project_resend_lead_update(
        event_type="delivered",
        current_status=LeadStatus.SENT.value,
        occurred_at="2026-04-16T10:00:00Z",
    )
    assert upd["outreach_delivered_at"] == "2026-04-16T10:00:00Z"
    assert upd["pipeline_status"] == LeadStatus.DELIVERED.value


def test_opened_advances_from_delivered() -> None:
    upd = project_resend_lead_update(
        event_type="opened",
        current_status=LeadStatus.DELIVERED.value,
        occurred_at="2026-04-16T11:00:00Z",
    )
    assert upd["outreach_opened_at"] == "2026-04-16T11:00:00Z"
    assert upd["pipeline_status"] == LeadStatus.OPENED.value


def test_opened_does_not_regress_from_clicked() -> None:
    upd = project_resend_lead_update(
        event_type="opened",
        current_status=LeadStatus.CLICKED.value,
        occurred_at="2026-04-16T11:00:00Z",
    )
    # Timestamp still lands, but pipeline_status must not roll back.
    assert upd["outreach_opened_at"] == "2026-04-16T11:00:00Z"
    assert "pipeline_status" not in upd


def test_clicked_advances_from_opened() -> None:
    upd = project_resend_lead_update(
        event_type="clicked",
        current_status=LeadStatus.OPENED.value,
        occurred_at="2026-04-16T12:00:00Z",
    )
    assert upd["outreach_clicked_at"] == "2026-04-16T12:00:00Z"
    assert upd["pipeline_status"] == LeadStatus.CLICKED.value


def test_bounced_forces_blacklisted_even_from_higher_state() -> None:
    upd = project_resend_lead_update(
        event_type="bounced",
        current_status=LeadStatus.CLICKED.value,
        occurred_at="2026-04-16T13:00:00Z",
    )
    # Blacklist is terminal and monotonically wins.
    assert upd["pipeline_status"] == LeadStatus.BLACKLISTED.value
    # Bounced has no dedicated timestamp column on leads.
    assert "outreach_delivered_at" not in upd


def test_complained_forces_blacklisted() -> None:
    upd = project_resend_lead_update(
        event_type="complained",
        current_status=LeadStatus.DELIVERED.value,
        occurred_at="2026-04-16T14:00:00Z",
    )
    assert upd["pipeline_status"] == LeadStatus.BLACKLISTED.value


def test_sent_event_is_no_op() -> None:
    upd = project_resend_lead_update(
        event_type="sent",
        current_status=LeadStatus.SENT.value,
        occurred_at="2026-04-16T09:00:00Z",
    )
    assert upd == {}


def test_delivery_delayed_is_no_op() -> None:
    upd = project_resend_lead_update(
        event_type="delivery_delayed",
        current_status=LeadStatus.SENT.value,
        occurred_at="2026-04-16T09:00:00Z",
    )
    assert upd == {}


def test_unknown_event_returns_empty_dict() -> None:
    upd = project_resend_lead_update(
        event_type="mystery",
        current_status=LeadStatus.SENT.value,
        occurred_at="2026-04-16T09:00:00Z",
    )
    assert upd == {}


def test_missing_timestamp_skips_lead_column() -> None:
    upd = project_resend_lead_update(
        event_type="delivered",
        current_status=LeadStatus.SENT.value,
        occurred_at=None,
    )
    # No timestamp → no column write, but pipeline still advances.
    assert "outreach_delivered_at" not in upd
    assert upd["pipeline_status"] == LeadStatus.DELIVERED.value


def test_delivered_on_fresh_new_lead_still_advances() -> None:
    upd = project_resend_lead_update(
        event_type="delivered",
        current_status=None,  # never been set
        occurred_at="2026-04-16T10:00:00Z",
    )
    assert upd["pipeline_status"] == LeadStatus.DELIVERED.value


# ---------------------------------------------------------------------------
# Campaign-row projection
# ---------------------------------------------------------------------------


def test_campaign_delivered_sets_delivered_status() -> None:
    out = project_resend_campaign_update("delivered")
    assert out == {"status": CampaignStatus.DELIVERED.value}


def test_campaign_opened_clicked_no_change() -> None:
    assert project_resend_campaign_update("opened") == {}
    assert project_resend_campaign_update("clicked") == {}


def test_campaign_bounced_fails_with_reason() -> None:
    out = project_resend_campaign_update("bounced")
    assert out["status"] == CampaignStatus.FAILED.value
    assert out["failure_reason"] == "bounced"


def test_campaign_complained_fails_with_reason() -> None:
    out = project_resend_campaign_update("complained")
    assert out["status"] == CampaignStatus.FAILED.value
    assert out["failure_reason"] == "complained"


def test_campaign_unknown_event_empty() -> None:
    assert project_resend_campaign_update("mystery") == {}


# ---------------------------------------------------------------------------
# Pixart (postal) lead projection
# ---------------------------------------------------------------------------


def test_pixart_printed_is_no_op() -> None:
    upd = project_pixart_lead_update(
        event_type="printed",
        current_status=LeadStatus.SENT.value,
        occurred_at="2026-04-20T08:00:00Z",
    )
    assert upd == {}


def test_pixart_shipped_is_no_op() -> None:
    upd = project_pixart_lead_update(
        event_type="shipped",
        current_status=LeadStatus.SENT.value,
        occurred_at="2026-04-20T10:00:00Z",
    )
    assert upd == {}


def test_pixart_delivered_advances_pipeline_and_sets_timestamp() -> None:
    upd = project_pixart_lead_update(
        event_type="delivered",
        current_status=LeadStatus.SENT.value,
        occurred_at="2026-04-22T14:00:00Z",
    )
    assert upd["outreach_delivered_at"] == "2026-04-22T14:00:00Z"
    assert upd["pipeline_status"] == LeadStatus.DELIVERED.value


def test_pixart_delivered_does_not_regress_from_clicked() -> None:
    # An already-clicked email lead gets a delivered postcard: the
    # timestamp lands (audit) but pipeline_status must not roll back.
    upd = project_pixart_lead_update(
        event_type="delivered",
        current_status=LeadStatus.CLICKED.value,
        occurred_at="2026-04-22T14:00:00Z",
    )
    assert upd["outreach_delivered_at"] == "2026-04-22T14:00:00Z"
    assert "pipeline_status" not in upd


def test_pixart_returned_does_not_blacklist() -> None:
    # Postal returns are data-quality, not consent — no pipeline status bump.
    upd = project_pixart_lead_update(
        event_type="returned",
        current_status=LeadStatus.DELIVERED.value,
        occurred_at="2026-04-25T09:00:00Z",
    )
    assert "pipeline_status" not in upd
    assert "outreach_delivered_at" not in upd


def test_pixart_unknown_event_returns_empty_dict() -> None:
    assert (
        project_pixart_lead_update(
            event_type="exploded",
            current_status=LeadStatus.SENT.value,
            occurred_at="2026-04-20T08:00:00Z",
        )
        == {}
    )


def test_pixart_delivered_without_timestamp_still_advances_pipeline() -> None:
    upd = project_pixart_lead_update(
        event_type="delivered",
        current_status=LeadStatus.SENT.value,
        occurred_at=None,
    )
    assert "outreach_delivered_at" not in upd
    assert upd["pipeline_status"] == LeadStatus.DELIVERED.value


# ---------------------------------------------------------------------------
# Pixart campaign projection
# ---------------------------------------------------------------------------


def test_pixart_campaign_delivered_sets_delivered() -> None:
    out = project_pixart_campaign_update("delivered")
    assert out == {"status": CampaignStatus.DELIVERED.value}


def test_pixart_campaign_returned_fails_with_reason() -> None:
    out = project_pixart_campaign_update("returned")
    assert out["status"] == CampaignStatus.FAILED.value
    assert out["failure_reason"] == "postal_returned"


def test_pixart_campaign_printed_shipped_no_change() -> None:
    assert project_pixart_campaign_update("printed") == {}
    assert project_pixart_campaign_update("shipped") == {}


def test_pixart_campaign_unknown_event_empty() -> None:
    assert project_pixart_campaign_update("mystery") == {}
