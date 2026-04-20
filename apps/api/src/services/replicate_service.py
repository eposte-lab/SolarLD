"""Replicate client — runs an image-to-image model that overlays PV
panels on a satellite tile.

We target ``stability-ai/sdxl`` with ControlNet Canny guidance:
``img2img`` preserves the roof geometry while the prompt replaces the
bare surface with a neat photovoltaic array. The model is swappable via
``settings.replicate_model_image2image`` — in practice we iterate on the
prompt + version hash, not the code.

The HTTP surface is dead simple:

  POST  https://api.replicate.com/v1/predictions        (create)
  GET   https://api.replicate.com/v1/predictions/{id}   (poll)

The prediction is done when ``status`` is ``succeeded``, ``failed`` or
``canceled``. Output is a list of URLs (we always take ``output[0]``).

This module exposes:

  * ``create_pv_rendering(before_url, prompt_ctx)``
        async end-to-end: create → poll → return output URL.
  * ``parse_prediction(raw)``
        pure-function parser — used by tests and by ``poll_prediction``.
  * ``render_prompt(ctx)``
        builds the textual prompt from the lead's data (brand colour,
        roof area, tier) without any I/O.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

REPLICATE_API_BASE = "https://api.replicate.com/v1"

# ``stability-ai/sdxl`` public version pinned 2026-Q1. Bump after prompt
# regression tests when a newer checkpoint is better calibrated.
DEFAULT_MODEL_VERSION = (
    "7762fd07cf82c948538e41f63f77d685e02b063e37e496e96eefd46c929f9bdc"
)

# A Replicate call at sdxl-img2img with ~40 inference steps costs
# ~ $0.0035 on an A40 shared. Round up for wall-clock inefficiency.
REPLICATE_COST_PER_CALL_CENTS = 1


class ReplicateError(Exception):
    """Raised when the Replicate API rejects the request."""


class ReplicateTimeout(ReplicateError):
    """Raised when a prediction is still running after our poll budget."""


@dataclass(slots=True)
class PredictionResult:
    """Outcome of a single Replicate prediction."""

    id: str
    status: str          # starting | processing | succeeded | failed | canceled
    output_url: str | None
    error: str | None
    logs: str | None

    @property
    def is_done(self) -> bool:
        return self.status in ("succeeded", "failed", "canceled")

    @property
    def is_success(self) -> bool:
        return self.status == "succeeded" and self.output_url is not None


@dataclass(slots=True)
class RenderingPromptContext:
    """Inputs for :func:`render_prompt` — everything the LLM template needs."""

    area_sqm: float | None = None
    exposure: str | None = None
    brand_primary_color: str | None = None
    subject_type: str = "unknown"


def render_prompt(ctx: RenderingPromptContext) -> str:
    """Build the Replicate text prompt for this lead.

    The prompt emphasises **photorealism** and **roof preservation** —
    Replicate tends to hallucinate houses when the prompt is too
    generic. We therefore name the specific building-type and mention
    'preserve building outline' explicitly.
    """
    type_hint = {
        "b2b": "commercial or industrial building",
        "b2c": "Italian private residential house",
    }.get(ctx.subject_type.lower(), "building")

    area_hint = ""
    if ctx.area_sqm and ctx.area_sqm >= 200:
        area_hint = ", large rooftop, industrial PV array"
    elif ctx.area_sqm:
        area_hint = f", approx {int(ctx.area_sqm)}m² rooftop"

    exposure_hint = ""
    if ctx.exposure and ctx.exposure.upper() in {"S", "SE", "SW"}:
        exposure_hint = ", perfectly aligned with sun azimuth"

    parts = [
        "aerial satellite view",
        type_hint,
        "high-resolution photograph",
        "freshly installed premium black monocrystalline photovoltaic "
        "panels covering the rooftop in neat uniform rows",
        "preserve the existing building outline and surroundings exactly",
        "realistic shadows and lighting",
        "ultra detailed, 4k, professional installation showcase",
    ]
    if area_hint:
        parts.append(area_hint.lstrip(", "))
    if exposure_hint:
        parts.append(exposure_hint.lstrip(", "))
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


def _auth_headers() -> dict[str, str]:
    if not settings.replicate_api_token:
        raise ReplicateError("REPLICATE_API_TOKEN not configured")
    return {
        "Authorization": f"Bearer {settings.replicate_api_token}",
        "Content-Type": "application/json",
    }


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def create_prediction(
    *,
    image_url: str,
    prompt: str,
    model_version: str = DEFAULT_MODEL_VERSION,
    negative_prompt: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> PredictionResult:
    """Kick off an img2img prediction, return the initial status."""
    payload = {
        "version": model_version,
        "input": {
            "image": image_url,
            "prompt": prompt,
            "negative_prompt": negative_prompt
            or "people, cars, text, watermark, blurry, distorted, low quality",
            "num_inference_steps": 40,
            "guidance_scale": 7.5,
            "prompt_strength": 0.55,   # keep ~45% of the original roof
        },
    }
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
    try:
        resp = await client.post(
            f"{REPLICATE_API_BASE}/predictions",
            headers=_auth_headers(),
            json=payload,
        )
    finally:
        if owns_client:
            await client.aclose()

    if resp.status_code >= 400:
        raise ReplicateError(
            f"predictions create status={resp.status_code} body={resp.text[:300]}"
        )
    return parse_prediction(resp.json())


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def fetch_prediction(
    prediction_id: str, *, client: httpx.AsyncClient | None = None
) -> PredictionResult:
    """Poll a prediction once."""
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.get(
            f"{REPLICATE_API_BASE}/predictions/{prediction_id}",
            headers=_auth_headers(),
        )
    finally:
        if owns_client:
            await client.aclose()
    if resp.status_code >= 400:
        raise ReplicateError(
            f"predictions get status={resp.status_code} body={resp.text[:300]}"
        )
    return parse_prediction(resp.json())


async def poll_prediction(
    prediction_id: str,
    *,
    client: httpx.AsyncClient | None = None,
    poll_interval_s: float = 2.0,
    max_wait_s: float = 120.0,
) -> PredictionResult:
    """Block until the prediction is done or we exhaust the budget."""
    elapsed = 0.0
    while True:
        result = await fetch_prediction(prediction_id, client=client)
        if result.is_done:
            return result
        if elapsed >= max_wait_s:
            raise ReplicateTimeout(
                f"prediction {prediction_id} still {result.status} after {max_wait_s}s"
            )
        await asyncio.sleep(poll_interval_s)
        elapsed += poll_interval_s


async def create_pv_rendering(
    *,
    before_image_url: str,
    prompt_ctx: RenderingPromptContext,
    model_version: str = DEFAULT_MODEL_VERSION,
    client: httpx.AsyncClient | None = None,
    max_attempts: int = 2,
) -> PredictionResult:
    """End-to-end: create + poll + return the final prediction.

    Retries the **whole flow** up to ``max_attempts`` times on:
      * ``ReplicateTimeout`` (prediction stuck in ``processing``)
      * a terminal ``status='failed'`` or ``status='canceled'``
        (the A40 shared queue occasionally OOMs on the first try —
        a second attempt usually lands on a different worker)

    Inner HTTP retries (via tenacity) handle transient 5xx and
    connection errors; this outer loop handles **semantic** failures
    specific to the Replicate prediction lifecycle.

    Raises ``ReplicateError`` on any upstream failure after exhausting
    attempts. Caller should treat this as "rendering unavailable — skip,
    retry on next agent run".
    """
    prompt = render_prompt(prompt_ctx)
    last_err: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            created = await create_prediction(
                image_url=before_image_url,
                prompt=prompt,
                model_version=model_version,
                client=client,
            )
            if created.is_done:
                # Replicate occasionally returns a cached result synchronously.
                if created.is_success:
                    return created
                last_err = ReplicateError(
                    f"prediction {created.id} ended {created.status}: "
                    f"{created.error}"
                )
                log.warning(
                    "replicate.prediction_failed",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    prediction_id=created.id,
                    status=created.status,
                    error=created.error,
                )
                continue  # retry
            result = await poll_prediction(created.id, client=client)
            if result.is_success:
                return result
            last_err = ReplicateError(
                f"prediction {result.id} ended {result.status}: {result.error}"
            )
            log.warning(
                "replicate.prediction_ended_unsuccessfully",
                attempt=attempt,
                max_attempts=max_attempts,
                prediction_id=result.id,
                status=result.status,
            )
        except ReplicateTimeout as exc:
            last_err = exc
            log.warning(
                "replicate.prediction_timeout",
                attempt=attempt,
                max_attempts=max_attempts,
                err=str(exc),
            )

    assert last_err is not None  # exhausted loop must have set this
    raise last_err


# ---------------------------------------------------------------------------
# Pure-function parser — testable without an HTTP round-trip
# ---------------------------------------------------------------------------


def parse_prediction(raw: dict[str, Any]) -> PredictionResult:
    """Project a Replicate JSON response to our dataclass."""
    pid = str(raw.get("id", ""))
    status = str(raw.get("status", "starting"))
    error = raw.get("error")
    logs = raw.get("logs")

    output_url: str | None = None
    output = raw.get("output")
    if isinstance(output, list) and output:
        first = output[0]
        if isinstance(first, str):
            output_url = first
    elif isinstance(output, str):
        output_url = output

    return PredictionResult(
        id=pid,
        status=status,
        output_url=output_url,
        error=str(error) if error else None,
        logs=str(logs) if logs else None,
    )
