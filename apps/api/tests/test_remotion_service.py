"""Unit tests for the Remotion sidecar client (pure helpers only).

The HTTP call itself isn't exercised — `build_render_request` +
`parse_render_response` cover the full contract between FastAPI and
the Node sidecar.
"""

from __future__ import annotations

import pytest

from src.services.remotion_service import (
    RemotionError,
    RenderTransitionInput,
    build_render_request,
    parse_render_response,
)


def _input(**overrides: object) -> RenderTransitionInput:
    base: dict[str, object] = {
        "before_image_url": "https://example.com/b.png",
        "after_image_url": "https://example.com/a.png",
        "kwp": 8.0,
        "yearly_savings_eur": 1200.0,
        "payback_years": 6.5,
        "tenant_name": "Solare Rapido SRL",
        "output_path": "tenant-abc/lead-123",
    }
    base.update(overrides)
    return RenderTransitionInput(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# build_render_request
# ---------------------------------------------------------------------------


def test_build_render_request_uses_camelcase_keys() -> None:
    body = build_render_request(_input())
    # Node zod schema expects camelCase
    for key in (
        "beforeImageUrl",
        "afterImageUrl",
        "kwp",
        "yearlySavingsEur",
        "paybackYears",
        "tenantName",
        "brandPrimaryColor",
        "outputPath",
        "bucket",
    ):
        assert key in body
    # And never snake_case
    for forbidden in ("before_image_url", "yearly_savings_eur", "output_path"):
        assert forbidden not in body


def test_build_render_request_defaults_bucket_and_brand_color() -> None:
    body = build_render_request(_input())
    assert body["bucket"] == "renderings"
    assert body["brandPrimaryColor"] == "#0F766E"


def test_build_render_request_omits_optional_fields_when_unset() -> None:
    body = build_render_request(_input())
    # co2 + logo are optional — absent unless explicitly set
    assert "co2TonnesLifetime" not in body
    assert "brandLogoUrl" not in body


def test_build_render_request_includes_optional_fields_when_set() -> None:
    body = build_render_request(
        _input(
            co2_tonnes_lifetime=91.3,
            brand_logo_url="https://example.com/logo.png",
            brand_primary_color="#ff0066",
        )
    )
    assert body["co2TonnesLifetime"] == 91.3
    assert body["brandLogoUrl"] == "https://example.com/logo.png"
    assert body["brandPrimaryColor"] == "#ff0066"


def test_build_render_request_coerces_numerics_to_floats() -> None:
    body = build_render_request(
        _input(kwp=10, yearly_savings_eur=1500, payback_years=7)
    )
    assert isinstance(body["kwp"], float)
    assert isinstance(body["yearlySavingsEur"], float)
    assert isinstance(body["paybackYears"], float)


# ---------------------------------------------------------------------------
# parse_render_response
# ---------------------------------------------------------------------------


def test_parse_render_response_happy_path() -> None:
    result = parse_render_response(
        {
            "mp4Url": "https://cdn/tenant/lead/transition.mp4",
            "gifUrl": "https://cdn/tenant/lead/transition.gif",
            "durationMs": 12345,
        }
    )
    assert result.mp4_url.endswith("transition.mp4")
    assert result.gif_url.endswith("transition.gif")
    assert result.duration_ms == 12345


def test_parse_render_response_missing_mp4_raises() -> None:
    with pytest.raises(RemotionError):
        parse_render_response({"gifUrl": "https://cdn/x.gif"})


def test_parse_render_response_missing_gif_raises() -> None:
    with pytest.raises(RemotionError):
        parse_render_response({"mp4Url": "https://cdn/x.mp4"})


def test_parse_render_response_empty_urls_raise() -> None:
    with pytest.raises(RemotionError):
        parse_render_response({"mp4Url": "", "gifUrl": ""})


def test_parse_render_response_tolerates_missing_duration() -> None:
    result = parse_render_response(
        {"mp4Url": "https://x.mp4", "gifUrl": "https://x.gif"}
    )
    assert result.duration_ms == 0


def test_parse_render_response_tolerates_garbage_duration() -> None:
    result = parse_render_response(
        {
            "mp4Url": "https://x.mp4",
            "gifUrl": "https://x.gif",
            "durationMs": "not-a-number",
        }
    )
    assert result.duration_ms == 0


def test_parse_render_response_non_string_url_is_rejected() -> None:
    with pytest.raises(RemotionError):
        parse_render_response({"mp4Url": 42, "gifUrl": "https://x.gif"})
