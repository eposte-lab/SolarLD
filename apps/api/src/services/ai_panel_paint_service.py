"""Panel-layer generation via Google Gemini 2.5 Flash Image
(``google/nano-banana`` on Replicate) + deterministic extraction.

Flow
----

  1. paint: nano-banana edits the real aerial photo, adding
     photorealistic panels on the rooftop — the "edit this photo"
     task it handles well, with the framing locked to the input.
  2. extract: the painted panels are stencilled out of that frame
     with the Solar-geometry panel mask. A before/after diff cannot
     be used — nano-banana re-encodes the *whole* image — and an AI
     "isolation" pass re-framed it; the Solar mask is deterministic
     and already aligned to the before crop.
  3. composite: the transparent panel layer is dropped over the
     untouched before image.

    before.png  (real aerial, never touched)
         +  panel layer  (painted panels, stencilled to a transparent field)
         =  after.png

The background is the before image by construction, so it is always
pixel-aligned; only the panels come from the model.

Override points (env)
---------------------

``REPLICATE_API_TOKEN``        — required
``AI_PAINT_MODEL_OWNER``       — defaults to ``google``
``AI_PAINT_MODEL_NAME``        — defaults to ``nano-banana``
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
from typing import Any

import httpx
from PIL import Image
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
    """Pass 1 — prompt nano-banana to paint panels onto the rooftop."""
    count = f"approximately {panel_count} " if panel_count > 0 else ""
    scale = f", together forming roughly a {kwp:.0f} kWp array" if kwp and kwp > 0 else ""
    return (
        "TASK: Edit this top-down aerial photograph by adding "
        "photorealistic monocrystalline silicon solar panels onto the "
        "rooftop of the principal building at the centre of the frame. "
        f"Add {count}dark-blue rectangular panels with thin silver "
        f"aluminium frames, in neat parallel rows aligned to the roof "
        f"edges{scale}, covering every usable roof segment of that "
        "building. The panels lie flat on the roof, follow its exact "
        "slope, perspective and orientation, and cast realistic soft "
        "shadows where they meet the surface. "
        "Place panels ONLY on the building's rooftop — never on lawn, "
        "garden, ground, driveway, parking, road, or neighbouring "
        "buildings. "
        "FRAMING LOCK: the output MUST keep the EXACT same pixel "
        "dimensions, framing, zoom and crop as the input — do not pan, "
        "zoom, rotate or re-crop. Preserve every non-roof pixel exactly "
        "as in the source. "
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
    poll_timeout_s: float = 120.0,
    poll_interval_s: float = 2.0,
    http_client: httpx.AsyncClient | None = None,
    model_owner: str | None = None,
    model_name: str | None = None,
) -> bytes:
    """Create a nano-banana prediction, poll, download the PNG bytes."""
    owner = model_owner or DEFAULT_MODEL_OWNER
    name = model_name or DEFAULT_MODEL_NAME
    create_url = _PREDICTIONS_URL_FMT.format(owner=owner, name=name)
    body = {
        "input": {
            "prompt": prompt,
            "image_input": [image_url],
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
    """Pass 1: edit the before image, return the after PNG with panels."""
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


# ---------------------------------------------------------------------------
# Panel-layer extraction + compositing (pure)
# ---------------------------------------------------------------------------


def _rgba_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, format="PNG")
    return buf.getvalue()


def extract_panel_layer(after_bytes: bytes, mask_bytes: bytes) -> bytes:
    """Stencil the painted panels out of the after frame.

    The after frame (nano-banana, photoreal panels, framing locked to
    the before) is cut with the Solar-geometry panel mask: inside the
    white mask we keep the painted panels, everything else becomes
    transparent.

    Why a mask and not a before/after diff: nano-banana re-encodes the
    *whole* image, so a per-pixel diff lights up everywhere, not just
    on the panels. The Solar mask is deterministic and already aligned
    to the before crop, so it isolates the panel region reliably.

    Returns an RGBA PNG: the painted panels on a transparent field.
    """
    after = Image.open(io.BytesIO(after_bytes)).convert("RGBA")
    mask = Image.open(io.BytesIO(mask_bytes)).convert("L")
    if mask.size != after.size:
        mask = mask.resize(after.size, Image.LANCZOS)

    after.putalpha(mask)

    opaque_px = sum(1 for v in mask.getdata() if v > 10)
    log.info(
        "ai_paint.panel_layer_stencil",
        coverage=round(opaque_px / max(1, mask.width * mask.height), 3),
    )
    return _rgba_png(after)


def composite_panel_layer(before_bytes: bytes, layer_bytes: bytes) -> bytes:
    """Composite the isolated panel layer over the untouched before image.

    The background stays exactly the before image; only the panels are
    drawn on top. Returns an RGB PNG.
    """
    before = Image.open(io.BytesIO(before_bytes)).convert("RGBA")
    layer = Image.open(io.BytesIO(layer_bytes)).convert("RGBA")
    if layer.size != before.size:
        layer = layer.resize(before.size, Image.LANCZOS)

    out = Image.alpha_composite(before, layer).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True, compress_level=6)
    return buf.getvalue()
