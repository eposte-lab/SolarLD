"""Remotion sidecar client — POSTs render requests to the Node service.

The sidecar lives in ``apps/video-renderer`` and exposes two routes:

    GET  /health   → { status, service, version }
    POST /render   → { mp4Url, gifUrl, durationMs }

We POST the composition props plus ``outputPath`` (which becomes
``renderings/{tenant_id}/{lead_id}/`` on Supabase Storage) and trust the
sidecar to do the bundle + render + upload. A typical render takes
8-15s on an M-series Mac and 15-30s on a shared Fly.io machine, so the
default client timeout is generous.

Split into pure helpers (`build_render_request`, `parse_render_response`)
+ one async entry point (`render_transition`) so tests can exercise the
happy + failure paths without the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)


class RemotionError(Exception):
    """Raised when the Remotion sidecar returns an error response."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RenderTransitionInput:
    """Shape of one POST /render request.

    The sidecar uses ``before_image_url`` as the conditioning frame for
    a hosted image-to-video model (Replicate / Kling) and produces the
    panel-reveal animation.  ``after_image_url`` is kept in the schema
    for backward compatibility with older callers (and for a future
    end-frame conditioning experiment) but is currently unused by the
    sidecar pipeline.
    """

    before_image_url: str
    after_image_url: str
    kwp: float
    yearly_savings_eur: float
    payback_years: float
    tenant_name: str
    output_path: str                          # e.g. "{tenant_id}/{lead_id}"
    co2_tonnes_lifetime: float | None = None
    brand_primary_color: str = "#0F766E"
    brand_logo_url: str | None = None
    bucket: str = "renderings"


@dataclass(slots=True)
class RenderTransitionResult:
    mp4_url: str
    gif_url: str
    duration_ms: int


# ---------------------------------------------------------------------------
# Pure helpers — fully testable
# ---------------------------------------------------------------------------


def build_render_request(data: RenderTransitionInput) -> dict[str, Any]:
    """Serialize the dataclass to the JSON body the sidecar expects.

    The Node side uses zod camelCase field names — we mirror them here
    rather than snake_case.
    """
    body: dict[str, Any] = {
        "beforeImageUrl": data.before_image_url,
        "afterImageUrl": data.after_image_url,
        "kwp": float(data.kwp),
        "yearlySavingsEur": float(data.yearly_savings_eur),
        "paybackYears": float(data.payback_years),
        "tenantName": data.tenant_name,
        "brandPrimaryColor": data.brand_primary_color,
        "outputPath": data.output_path,
        "bucket": data.bucket,
    }
    if data.co2_tonnes_lifetime is not None:
        body["co2TonnesLifetime"] = float(data.co2_tonnes_lifetime)
    if data.brand_logo_url:
        body["brandLogoUrl"] = data.brand_logo_url
    return body


def parse_render_response(raw: dict[str, Any]) -> RenderTransitionResult:
    """Project the sidecar JSON response into our dataclass.

    Fails loud when either URL is missing — there's no partial-success
    story for this endpoint (we want both MP4 *and* GIF; one without
    the other is useless for outreach).
    """
    mp4 = raw.get("mp4Url")
    gif = raw.get("gifUrl")
    if not isinstance(mp4, str) or not mp4:
        raise RemotionError(f"missing mp4Url in response: {raw!r}")
    if not isinstance(gif, str) or not gif:
        raise RemotionError(f"missing gifUrl in response: {raw!r}")
    try:
        duration = int(raw.get("durationMs") or 0)
    except (TypeError, ValueError):
        duration = 0
    return RenderTransitionResult(mp4_url=mp4, gif_url=gif, duration_ms=duration)


# ---------------------------------------------------------------------------
# HTTP entry point
# ---------------------------------------------------------------------------


def _sidecar_url() -> str:
    base = getattr(settings, "video_renderer_url", None) or "http://localhost:4000"
    return base.rstrip("/")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def render_transition(
    data: RenderTransitionInput,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_s: float = 180.0,
) -> RenderTransitionResult:
    """Call the sidecar and return the uploaded URLs.

    Retries 3x on transient errors (timeouts, 5xx). A 4xx is treated as
    permanent — ``RemotionError`` bubbles up after the first attempt so
    the CreativeAgent can skip the video step and carry on.
    """
    body = build_render_request(data)
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=timeout_s)
    target = f"{_sidecar_url()}/render"
    log.info("remotion.sidecar_call", url=target, lead_id=getattr(data, "lead_id", None))
    try:
        try:
            resp = await client.post(target, json=body)
        except httpx.ConnectError as exc:
            # DNS / IPv6 / wrong service name → bubble up with the URL we tried
            log.error(
                "remotion.sidecar_connect_error",
                url=target,
                err=str(exc),
                err_type=type(exc).__name__,
            )
            raise RemotionError(f"sidecar connect_error url={target} err={exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    if resp.status_code >= 500:
        # Let tenacity retry
        raise RemotionError(
            f"sidecar 5xx status={resp.status_code} body={resp.text[:300]}"
        )
    if resp.status_code >= 400:
        # Permanent — caller shouldn't retry, skip the video step.
        raise RemotionError(
            f"sidecar 4xx status={resp.status_code} body={resp.text[:300]}"
        )
    try:
        return parse_render_response(resp.json())
    except ValueError as exc:  # json decode
        raise RemotionError(f"sidecar non-json response: {exc}") from exc
