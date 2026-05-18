"""After-frame generation via Google Gemini 2.5 Flash Image
(``google/nano-banana`` on Replicate).

nano-banana edits the real aerial "before" photo, adding photorealistic
solar panels on the rooftop. The prompt does two jobs: identify the
correct rooftop (the target building, all its segments) and place the
panels on it, and lock the framing to the input so the painted "after"
frame overlays the "before" as precisely as possible. The painted
frame is used directly as the after; the transition is a normal
before→after crossfade.

Override points (env)
---------------------

``REPLICATE_API_TOKEN``        — required
``AI_PAINT_MODEL_OWNER``       — defaults to ``google``
``AI_PAINT_MODEL_NAME``        — defaults to ``nano-banana``
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)


REPLICATE_API_BASE = "https://api.replicate.com/v1"

# Env-overridable so the engine can be swapped without a redeploy.
DEFAULT_MODEL_OWNER = os.getenv("AI_PAINT_MODEL_OWNER", "google")
DEFAULT_MODEL_NAME = os.getenv("AI_PAINT_MODEL_NAME", "nano-banana")

_PREDICTIONS_URL_FMT = REPLICATE_API_BASE + "/models/{owner}/{name}/predictions"
_PREDICTION_URL_FMT = REPLICATE_API_BASE + "/predictions/{pred_id}"


class AiPaintError(Exception):
    """Raised when the AI call fails to return a usable image."""


# ---------------------------------------------------------------------------
# Prompt construction (pure)
# ---------------------------------------------------------------------------


def build_paint_prompt(*, panel_count: int, kwp: float | None = None) -> str:
    """Prompt nano-banana to paint panels onto the rooftop."""
    count = f"approximately {panel_count} " if panel_count > 0 else ""
    scale = f", together forming roughly a {kwp:.0f} kWp array" if kwp and kwp > 0 else ""
    return (
        "TASK: Edit this top-down aerial photograph by adding "
        "photorealistic solar panels to a rooftop. "
        # ── 1. Identify the correct rooftop ──────────────────────────
        "FIRST identify the correct rooftop: the principal building "
        "occupying the centre of the frame. Its rooftop is the "
        "contiguous elevated surface bounded by the building's outer "
        "walls — recognisable as roof tiles, sheet metal, corrugated "
        "panels, shingles or a flat membrane, and clearly raised above "
        "the surrounding ground. Identify ALL of that building's roof "
        "segments, including separate or differently-angled sections. "
        # ── 2. Place the panels ──────────────────────────────────────
        f"Add {count}photorealistic dark-blue monocrystalline silicon "
        f"panels with thin silver aluminium frames{scale}, in neat "
        "parallel rows aligned to the roof edges, covering every "
        "usable roof segment of that building. The panels lie flat on "
        "the roof, follow its exact slope, perspective and "
        "orientation, and cast realistic soft shadows where they meet "
        "the surface. "
        # ── 3. Strict boundaries ─────────────────────────────────────
        "Place panels ONLY on that building's rooftop. NEVER place "
        "panels on the ground, lawn, garden, courtyard, driveway, "
        "parking, road, or on neighbouring buildings. If a row would "
        "run past a roof edge, shorten it rather than overhang. "
        # ── 4. Framing lock — alignment with the before frame ───────
        "CRITICAL FRAMING LOCK: the output MUST keep the EXACT same "
        "pixel dimensions, framing, zoom, crop and camera position as "
        "the input image. Do NOT pan, zoom, rotate or re-crop. Every "
        "roof edge, wall, road, vehicle and tree must stay at the "
        "IDENTICAL pixel position as in the input — the ONLY change "
        "from input to output is the added panels, so the two frames "
        "overlay each other perfectly. "
        # ── 5. Output ────────────────────────────────────────────────
        "Output: photorealistic, top-down aerial perspective, natural "
        "daylight, sharp focus, no text, no watermarks."
    )


# ---------------------------------------------------------------------------
# HTTP / Replicate plumbing
# ---------------------------------------------------------------------------


def _auth_headers() -> dict[str, str]:
    if not settings.replicate_api_token:
        raise AiPaintError("REPLICATE_API_TOKEN not configured")
    return {
        "Authorization": f"Bearer {settings.replicate_api_token}",
        "Content-Type": "application/json",
    }


def _extract_output_url(output: Any) -> str | None:
    """nano-banana returns either a URL string or a list of URL strings."""
    if isinstance(output, str) and output.startswith("http"):
        return output
    if isinstance(output, list) and output:
        first = output[0]
        if isinstance(first, str) and first.startswith("http"):
            return first
    return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
async def _run_nano_banana(
    prompt: str,
    image_url: str,
    *,
    extra_image_urls: list[str] | None = None,
    poll_timeout_s: float = 120.0,
    poll_interval_s: float = 2.0,
    http_client: httpx.AsyncClient | None = None,
    model_owner: str | None = None,
    model_name: str | None = None,
) -> bytes:
    """Create a nano-banana prediction, poll, download the PNG bytes.

    ``extra_image_urls`` are appended after the primary image — used to
    pass a placement mask as a second reference image.
    """
    owner = model_owner or DEFAULT_MODEL_OWNER
    name = model_name or DEFAULT_MODEL_NAME
    create_url = _PREDICTIONS_URL_FMT.format(owner=owner, name=name)
    body = {
        "input": {
            "prompt": prompt,
            "image_input": [image_url, *(extra_image_urls or [])],
            "output_format": "png",
        }
    }

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=60.0)
    )

    try:
        resp = await client.post(
            create_url,
            headers={**_auth_headers(), "Prefer": "wait=10"},
            json=body,
        )
        if resp.status_code == 429:
            retry_after_s = 30.0
            ra_header = resp.headers.get("Retry-After")
            if ra_header:
                with contextlib.suppress(ValueError):
                    retry_after_s = float(ra_header)
            log.warning("ai_paint.rate_limited", retry_after_s=retry_after_s)
            await asyncio.sleep(min(retry_after_s, 60.0))
            resp = await client.post(
                create_url,
                headers={**_auth_headers(), "Prefer": "wait=10"},
                json=body,
            )
        if resp.status_code >= 400:
            raise AiPaintError(f"create status={resp.status_code} body={resp.text[:300]}")

        payload = resp.json()
        pred_id = payload.get("id")
        status = payload.get("status", "")
        output = payload.get("output")
        error = payload.get("error")

        deadline = asyncio.get_event_loop().time() + poll_timeout_s
        while status not in ("succeeded", "failed", "canceled"):
            if asyncio.get_event_loop().time() > deadline:
                raise AiPaintError(f"prediction {pred_id} timed out")
            await asyncio.sleep(poll_interval_s)
            r = await client.get(
                _PREDICTION_URL_FMT.format(pred_id=pred_id),
                headers=_auth_headers(),
            )
            if r.status_code >= 400:
                raise AiPaintError(f"poll {pred_id} status={r.status_code} body={r.text[:200]}")
            j = r.json()
            status = j.get("status", "")
            output = j.get("output")
            error = j.get("error")

        if status != "succeeded":
            raise AiPaintError(f"prediction {pred_id} ended {status}: {error or 'unknown'}")

        out_url = _extract_output_url(output)
        if not out_url:
            raise AiPaintError(f"no output URL in response (output={output!r})")

        dl = await client.get(out_url, timeout=60.0)
        if dl.status_code != 200:
            raise AiPaintError(f"download {out_url[:80]} status={dl.status_code}")

        log.info("ai_paint.done", prediction_id=pred_id, output_kb=len(dl.content) // 1024)
        return dl.content
    finally:
        if owns_client:
            await client.aclose()


async def generate_after_with_panels(
    *,
    before_image_url: str,
    panel_count: int,
    kwp: float | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> bytes:
    """Paint panels on the before image, return the after PNG."""
    log.info(
        "ai_paint.paint_start",
        before_url_peek=before_image_url[:100],
        panel_count=panel_count,
        kwp=kwp,
    )
    return await _run_nano_banana(
        build_paint_prompt(panel_count=panel_count, kwp=kwp),
        before_image_url,
        http_client=http_client,
    )


def build_masked_paint_prompt(*, panel_count: int, kwp: float | None = None) -> str:
    """Prompt nano-banana to paint panels guided by a placement mask.

    Unlike ``build_paint_prompt`` (which makes the model find the roof
    itself), this passes the Solar API panel footprint as a second
    mask image, so the model is told exactly WHERE the panels go.
    """
    count = f"approximately {panel_count} " if panel_count > 0 else ""
    scale = f", together forming roughly a {kwp:.0f} kWp array" if kwp and kwp > 0 else ""
    return (
        "You are given TWO images. IMAGE 1 is a top-down aerial "
        "photograph of a building. IMAGE 2 is a black-and-white "
        "placement mask, pixel-aligned to IMAGE 1: the WHITE area marks "
        "the EXACT rooftop region where solar panels must be installed. "
        "TASK: edit IMAGE 1 by adding "
        f"{count}photorealistic dark-blue monocrystalline silicon solar "
        f"panels with thin silver aluminium frames{scale}, in neat "
        "parallel rows aligned to the roof edges, covering precisely "
        "the region marked white in IMAGE 2. The panels lie flat on the "
        "roof, follow its exact slope, perspective and orientation, and "
        "cast realistic soft shadows where they meet the surface. "
        "STRICT: place panels ONLY where IMAGE 2 is white. Add nothing "
        "where IMAGE 2 is black — no panels on the ground, lawn, "
        "courtyard, road or neighbouring roofs. Do NOT render the mask "
        "itself or any tint in the output. "
        "CRITICAL FRAMING LOCK: keep the EXACT pixel dimensions, "
        "framing, zoom, crop and camera position of IMAGE 1. Every roof "
        "edge, wall, road, vehicle and tree must stay at the IDENTICAL "
        "pixel position — the ONLY change is the added panels. "
        "Output: the edited IMAGE 1 only — photorealistic, top-down "
        "aerial perspective, natural daylight, sharp focus, no text, no "
        "watermarks."
    )


async def generate_after_masked(
    *,
    before_image_url: str,
    mask_image_url: str,
    panel_count: int,
    kwp: float | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> bytes:
    """Paint panels guided by a placement mask.

    nano-banana receives the mask as a second reference image and is
    told to paint photoreal panels only inside its white region. The
    caller still composites the result through the mask for a hard
    off-roof guarantee.
    """
    log.info(
        "ai_paint.masked_paint_start",
        before_url_peek=before_image_url[:100],
        mask_url_peek=mask_image_url[:100],
        panel_count=panel_count,
        kwp=kwp,
    )
    return await _run_nano_banana(
        build_masked_paint_prompt(panel_count=panel_count, kwp=kwp),
        before_image_url,
        extra_image_urls=[mask_image_url],
        http_client=http_client,
    )
