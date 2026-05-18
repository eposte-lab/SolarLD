"""Isolated-panel layer generation via Google Gemini 2.5 Flash Image
(``google/nano-banana`` on Replicate).

Why this approach
-----------------

Earlier attempts each failed on one axis:

* unmasked nano-banana edit → re-frames the whole photo, before/after
  never aligned;
* masked FLUX Fill inpaint → aligned, but the panels it paints inside
  the mask look flat and unconvincing.

This service asks nano-banana for something narrower: look at the real
aerial photo, work out how panels would sit on that exact rooftop, and
output ONLY the panels — every other pixel transparent (or, failing
transparency, a solid green that we chroma-key out). We then composite
that isolated panel layer over the untouched "before" image:

    before.png  (real aerial, never touched)
         +  panel layer  (nano-banana panels, background removed)
         =  after.png

The background is the before image by construction, so it is always
pixel-aligned; only the panels come from the model. If nano-banana
shifts the panels slightly they simply sit slightly off on the roof —
there is no whole-frame jump, because there is no background to drift.

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
from PIL import Image, ImageChops, ImageFilter
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


def build_isolated_panel_prompt(*, panel_count: int, kwp: float | None = None) -> str:
    """Prompt nano-banana to output ONLY the panels, background removed.

    The model sees the real rooftop and works out the panel layout from
    it, but the output must contain nothing except the panels — so we
    can drop that layer straight onto our own untouched before image.
    """
    count = f"approximately {panel_count} " if panel_count > 0 else ""
    scale = f", together forming roughly a {kwp:.0f} kWp array" if kwp and kwp > 0 else ""
    return (
        "TASK: This is a top-down aerial photograph of a building. "
        "Identify the rooftop of the principal building at the centre "
        "of the frame. Work out exactly how solar panels would be "
        "installed on THAT rooftop — following its real shape, its "
        "separate roof segments, edges, ridge lines, orientation, "
        "slope, perspective and size. "
        f"Then output an image that contains ONLY those solar panels "
        "and NOTHING else. "
        f"Draw {count}photorealistic dark monocrystalline silicon "
        f"panels with thin silver aluminium frames, in neat parallel "
        f"rows aligned to the roof edges{scale}, each panel placed and "
        "sized exactly where it would physically sit on the rooftop. "
        # ── Output rules ─────────────────────────────────────────────
        "CRITICAL OUTPUT RULES: "
        "1. The output must contain the panels ONLY. Remove the roof, "
        "the building, the ground, vegetation, vehicles, roads and sky "
        "— replace every non-panel pixel with full transparency. If "
        "transparency is not possible, use a solid pure-green "
        "background (RGB 0,255,0) with no other colour. "
        "2. Do NOT draw the roof, the roof outline, or the building. "
        "Only the panels themselves are visible. "
        "3. The output image MUST keep the EXACT same pixel "
        "dimensions, framing and zoom as the input image, so every "
        "panel stays at the precise position it occupies on the roof. "
        "Do not pan, zoom, rotate or re-crop. "
        "Output: top-down aerial perspective, photorealistic panels, "
        "natural daylight, no text, no watermarks."
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


async def generate_isolated_panels(
    *,
    before_image_url: str,
    panel_count: int,
    kwp: float | None = None,
    http_client: httpx.AsyncClient | None = None,
    model_owner: str | None = None,
    model_name: str | None = None,
) -> bytes:
    """Return nano-banana's raw output: panels only, background removed.

    The caller passes the bytes through :func:`extract_panel_layer` to
    obtain a clean RGBA panel layer, then :func:`composite_panel_layer`
    to drop it onto the before image.
    """
    prompt = build_isolated_panel_prompt(panel_count=panel_count, kwp=kwp)
    log.info(
        "ai_paint.isolated_start",
        before_url_peek=before_image_url[:100],
        panel_count=panel_count,
        kwp=kwp,
    )
    return await _run_nano_banana(
        prompt,
        before_image_url,
        http_client=http_client,
        model_owner=model_owner,
        model_name=model_name,
    )


# ---------------------------------------------------------------------------
# Panel-layer extraction + compositing (pure)
# ---------------------------------------------------------------------------


def _rgba_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, format="PNG")
    return buf.getvalue()


def extract_panel_layer(raw_bytes: bytes) -> bytes:
    """Turn nano-banana's output into a clean RGBA panel layer.

    nano-banana is asked to output the panels on a transparent (or, as
    a fallback, solid green) background. If genuine transparency is
    present we keep it; otherwise we chroma-key the green out. Returns
    an RGBA PNG where everything that is not a panel is transparent.
    """
    img = Image.open(io.BytesIO(raw_bytes))

    # Case 1: the model already gave us real transparency.
    if img.mode == "RGBA":
        lo, _hi = img.getchannel("A").getextrema()
        if lo < 250:
            return _rgba_png(img)

    # Case 2: chroma-key a green background. A pixel is background when
    # green clearly dominates red and blue; panels (blue/black/silver)
    # never do, so this keys cleanly.
    rgba = img.convert("RGBA")
    r, g, b, _a = rgba.split()
    rb_max = ImageChops.lighter(r, b)
    greenness = ImageChops.subtract(g, rb_max)  # bright where green dominates
    alpha = greenness.point(lambda v: 0 if v > 60 else 255)
    alpha = alpha.filter(ImageFilter.GaussianBlur(1.0))
    rgba.putalpha(alpha)

    opaque_px = sum(1 for v in alpha.getdata() if v > 10)
    log.info(
        "ai_paint.panel_layer_keyed",
        opaque_fraction=round(opaque_px / max(1, alpha.width * alpha.height), 3),
    )
    return _rgba_png(rgba)


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
