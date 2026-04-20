"""Unit tests for the Replicate service's pure helpers.

We don't touch the network — ``parse_prediction`` and ``render_prompt``
are pure functions and cover ~80% of the surface area.
"""

from __future__ import annotations

from src.services.replicate_service import (
    RenderingPromptContext,
    parse_prediction,
    render_prompt,
)


# ---------------------------------------------------------------------------
# parse_prediction
# ---------------------------------------------------------------------------


def test_parse_prediction_succeeded_takes_first_output_url() -> None:
    raw = {
        "id": "abc123",
        "status": "succeeded",
        "output": [
            "https://replicate.delivery/xyz/first.png",
            "https://replicate.delivery/xyz/second.png",
        ],
        "error": None,
        "logs": "step 1...\nstep 40...",
    }
    result = parse_prediction(raw)
    assert result.id == "abc123"
    assert result.status == "succeeded"
    assert result.output_url == "https://replicate.delivery/xyz/first.png"
    assert result.is_done is True
    assert result.is_success is True
    assert result.error is None


def test_parse_prediction_failed_status_is_done_but_not_success() -> None:
    raw = {
        "id": "def456",
        "status": "failed",
        "output": None,
        "error": "NSFW content detected",
    }
    result = parse_prediction(raw)
    assert result.is_done is True
    assert result.is_success is False
    assert result.output_url is None
    assert result.error == "NSFW content detected"


def test_parse_prediction_processing_is_not_done() -> None:
    result = parse_prediction({"id": "x", "status": "processing"})
    assert result.is_done is False
    assert result.is_success is False
    assert result.output_url is None


def test_parse_prediction_handles_string_output() -> None:
    # Some model versions return a bare URL string rather than a list.
    result = parse_prediction(
        {
            "id": "s",
            "status": "succeeded",
            "output": "https://replicate.delivery/solo.png",
        }
    )
    assert result.output_url == "https://replicate.delivery/solo.png"
    assert result.is_success is True


def test_parse_prediction_empty_output_list() -> None:
    result = parse_prediction({"id": "e", "status": "succeeded", "output": []})
    # No URL present → success flag must be False so callers skip it.
    assert result.is_success is False
    assert result.output_url is None


def test_parse_prediction_missing_status_defaults_to_starting() -> None:
    result = parse_prediction({"id": "no-status"})
    assert result.status == "starting"
    assert result.is_done is False


def test_parse_prediction_canceled_status_is_terminal() -> None:
    result = parse_prediction({"id": "c", "status": "canceled"})
    assert result.is_done is True
    assert result.is_success is False


def test_parse_prediction_tolerates_non_string_first_output() -> None:
    # Defensive — if Replicate ever changes shape we'd rather return
    # output_url=None than crash the agent.
    result = parse_prediction(
        {"id": "q", "status": "succeeded", "output": [{"url": "..."}]}
    )
    assert result.output_url is None
    assert result.is_success is False


# ---------------------------------------------------------------------------
# render_prompt
# ---------------------------------------------------------------------------


def test_render_prompt_contains_photorealism_keywords() -> None:
    ctx = RenderingPromptContext(subject_type="b2c")
    prompt = render_prompt(ctx)
    # Core non-negotiable cues. If we ever drop one of these, the
    # downstream image quality regresses visibly.
    for needle in (
        "aerial satellite view",
        "photovoltaic",
        "preserve the existing building outline",
        "realistic shadows",
    ):
        assert needle in prompt


def test_render_prompt_b2b_describes_commercial_building() -> None:
    prompt = render_prompt(RenderingPromptContext(subject_type="b2b"))
    assert "commercial" in prompt or "industrial" in prompt


def test_render_prompt_b2c_describes_residential_building() -> None:
    prompt = render_prompt(RenderingPromptContext(subject_type="b2c"))
    assert "residential" in prompt


def test_render_prompt_large_area_adds_industrial_cue() -> None:
    small = render_prompt(
        RenderingPromptContext(area_sqm=50.0, subject_type="b2c")
    )
    big = render_prompt(
        RenderingPromptContext(area_sqm=500.0, subject_type="b2b")
    )
    assert "large rooftop" in big or "industrial PV array" in big
    # The small-area variant shouldn't be "industrial"-tagged.
    assert "large rooftop" not in small


def test_render_prompt_south_exposure_mentions_sun_azimuth() -> None:
    s = render_prompt(RenderingPromptContext(exposure="S", subject_type="b2c"))
    n = render_prompt(RenderingPromptContext(exposure="N", subject_type="b2c"))
    assert "sun azimuth" in s
    assert "sun azimuth" not in n


def test_render_prompt_unknown_subject_falls_back_generic() -> None:
    # Empty subject_type defaults to "unknown" → "building" generic hint.
    prompt = render_prompt(RenderingPromptContext(subject_type="sasquatch"))
    assert "building" in prompt


def test_render_prompt_is_deterministic() -> None:
    ctx = RenderingPromptContext(
        area_sqm=250.0,
        exposure="SW",
        brand_primary_color="#0F766E",
        subject_type="b2b",
    )
    assert render_prompt(ctx) == render_prompt(ctx)
