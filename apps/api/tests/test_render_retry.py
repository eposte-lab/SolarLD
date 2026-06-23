"""Unit tests for the self-healing render-retry logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.services.render_retry_service import (
    MAX_RENDER_RETRIES,
    backoff_seconds,
    is_eligible,
    is_retryable,
    reason_prefix,
)

NOW = datetime(2026, 6, 23, 18, 0, tzinfo=UTC)


def test_reason_prefix_strips_detail() -> None:
    assert reason_prefix("solar_render_error: 403 Forbidden") == "solar_render_error"
    assert reason_prefix("remotion_error") == "remotion_error"
    assert reason_prefix(None) == ""


def test_transient_reasons_are_retryable() -> None:
    for r in (
        "solar_render_error: boom",
        "solar_api_key_not_configured",
        "replicate_token_not_configured",
        "ai_paint_error: x",
        "render_unexpected: y",
        "remotion_error",
    ):
        assert is_retryable(r), r


def test_permanent_reasons_are_not_retryable() -> None:
    for r in ("missing_coords", "roof_confidence_too_low: mapbox_hq", "solar_no_building"):
        assert not is_retryable(r), r


def test_backoff_is_monotonic_and_capped() -> None:
    assert backoff_seconds(0) == 900  # 15 min
    assert backoff_seconds(1) == 1800  # 30 min
    assert backoff_seconds(2) == 3600  # 1 h
    # Monotonic non-decreasing
    seq = [backoff_seconds(i) for i in range(0, 12)]
    assert seq == sorted(seq)
    # Capped at 6h
    assert backoff_seconds(99) == 6 * 3600


def test_never_tried_lead_is_eligible_now() -> None:
    lead = {
        "creative_skipped_reason": "solar_render_error: 403",
        "render_retry_count": 0,
        "render_retry_at": None,
    }
    assert is_eligible(lead, NOW)


def test_recently_tried_lead_waits_for_backoff() -> None:
    lead = {
        "creative_skipped_reason": "solar_render_error",
        "render_retry_count": 0,
        "render_retry_at": (NOW - timedelta(minutes=5)).isoformat(),  # < 15 min
    }
    assert not is_eligible(lead, NOW)
    # 16 min later → past the 15-min window
    lead["render_retry_at"] = (NOW - timedelta(minutes=16)).isoformat()
    assert is_eligible(lead, NOW)


def test_permanent_reason_never_eligible() -> None:
    lead = {
        "creative_skipped_reason": "roof_confidence_too_low",
        "render_retry_count": 0,
        "render_retry_at": None,
    }
    assert not is_eligible(lead, NOW)


def test_exhausted_budget_not_eligible() -> None:
    lead = {
        "creative_skipped_reason": "solar_render_error",
        "render_retry_count": MAX_RENDER_RETRIES,
        "render_retry_at": None,
    }
    assert not is_eligible(lead, NOW)
