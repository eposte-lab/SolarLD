"""A/B variant stats compute open/click rate on REALLY-SENT leads only.

A failed send never reached the prospect, so it must not dilute the
denominator. These tests pin that contract on the pure aggregation helper.
"""

from __future__ import annotations

from src.routes.experiments import _compute_variant_stats


def _camp(variant: str, lead_id: str, status: str = "sent") -> dict:
    return {"experiment_variant": variant, "lead_id": lead_id, "status": status}


def test_failed_sends_excluded_from_denominator() -> None:
    # Variant A: 2 sent + 1 failed → denominator must be 2, not 3.
    campaigns = [
        _camp("a", "l1", "sent"),
        _camp("a", "l2", "sent"),
        _camp("a", "l3", "failed"),
    ]
    signals = {"l1": {"outreach_opened_at": "2026-06-20T10:00:00Z"}}

    stats = _compute_variant_stats(campaigns, signals)

    assert stats["a"].sends == 2  # the failed send is not a denominator
    assert stats["a"].opens == 1
    assert stats["a"].open_rate == 0.5  # 1 / 2, NOT 1 / 3


def test_open_of_a_failed_send_lead_is_not_counted() -> None:
    # A lead whose only send FAILED is neither in the denominator nor the
    # numerator, so the rate can never exceed 1.
    campaigns = [_camp("b", "lx", "failed")]
    signals = {"lx": {"outreach_opened_at": "2026-06-20T10:00:00Z"}}

    stats = _compute_variant_stats(campaigns, signals)

    assert stats["b"].sends == 0
    assert stats["b"].opens == 0
    assert stats["b"].open_rate == 0.0


def test_delivered_status_counts_and_distinct_leads() -> None:
    # 'delivered' counts like 'sent'; a lead with two sends counts once.
    campaigns = [
        _camp("a", "l1", "delivered"),
        _camp("a", "l1", "sent"),  # same lead, second step
        _camp("a", "l2", "sent"),
    ]
    signals = {
        "l1": {"outreach_opened_at": "t", "outreach_clicked_at": "t"},
        "l2": {"outreach_opened_at": "t"},
    }

    stats = _compute_variant_stats(campaigns, signals)

    assert stats["a"].sends == 2  # distinct leads
    assert stats["a"].opens == 2
    assert stats["a"].clicks == 1
    assert stats["a"].click_rate == 0.5


def test_no_sent_leaves_zero_rates() -> None:
    stats = _compute_variant_stats([], {})
    assert stats["a"].sends == 0
    assert stats["a"].open_rate == 0.0
    assert stats["b"].click_rate == 0.0
