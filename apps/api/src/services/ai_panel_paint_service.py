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
    """Prompt nano-banana to paint panels onto the rooftop.

    The prompt does three jobs, in order: lock onto the *correct*
    building, cover its roof *completely* with a realistic array, and
    keep the framing pixel-identical to the input.
    """
    count = f"approximately {panel_count} " if panel_count > 0 else ""
    scale = f", together forming roughly a {kwp:.0f} kWp array" if kwp and kwp > 0 else ""
    return (
        "TASK: Edit this top-down aerial photograph by installing a "
        "complete, photorealistic rooftop solar array on ONE building. "
        # ── 1. Identify the correct rooftop ──────────────────────────
        "STEP 1 — IDENTIFY THE TARGET ROOF. The target is the single "
        "main building whose rooftop occupies the CENTRE of the frame: "
        "the largest contiguous roof surface closest to the middle of "
        "the image. Its rooftop is the elevated surface bounded by that "
        "building's own outer walls — recognisable as a flat membrane, "
        "sheet metal, corrugated panels, roof tiles or shingles, and "
        "clearly raised above the surrounding ground. Trace its full "
        "perimeter and include EVERY roof segment that belongs to it, "
        "including separate wings, differently-angled pitches and "
        "lower annexes of the SAME building. Do not confuse it with "
        "adjacent buildings, internal courtyards or paved areas. "
        # ── 2. Cover the roof completely ─────────────────────────────
        f"STEP 2 — INSTALL THE PANELS. Add {count}photorealistic solar "
        f"panels{scale} and cover the ENTIRE usable area of that roof, "
        "edge to edge: the finished array must look like a real, fully "
        "built rooftop installation, not a small partial cluster. Lay "
        "the panels in neat continuous parallel rows aligned to the "
        "longest roof edge, with uniform module size and consistent "
        "small gaps between rows. Leave only realistic technical "
        "clearances: a narrow perimeter walkway just inside the roof "
        "edge, and tidy gaps around skylights, chimneys, HVAC units, "
        "stairwells and other rooftop obstacles — keep those obstacles "
        "visible and unpainted. The panels lie flat on the roof and "
        "follow its exact slope, perspective and orientation. "
        # ── 3. Panel realism ─────────────────────────────────────────
        "STEP 3 — REALISM. The panels are modern dark monocrystalline "
        "silicon modules: near-black/very dark blue, low-glare, with "
        "thin silver aluminium frames and a faint visible cell grid. "
        "Render realistic soft shadows where each row meets the roof "
        "and a subtle specular sheen consistent with the sun direction, "
        "exposure and resolution of the surrounding photograph, so the "
        "array looks photographed, not pasted. "
        # ── 4. Strict boundaries ─────────────────────────────────────
        "STRICT BOUNDARIES: place panels ONLY on the target building's "
        "rooftop. NEVER place panels on the ground, lawn, garden, "
        "courtyard, driveway, parking, road, or on any neighbouring "
        "building. If a row would run past a roof edge, shorten it "
        "rather than let it overhang. "
        # ── 5. Framing lock — alignment with the before frame ───────
        "CRITICAL FRAMING LOCK: the output MUST keep the EXACT same "
        "pixel dimensions, framing, zoom, crop and camera position as "
        "the input image. Do NOT pan, zoom, rotate or re-crop. Every "
        "roof edge, wall, road, vehicle and tree must stay at the "
        "IDENTICAL pixel position as in the input — the ONLY change "
        "from input to output is the added panels, so the two frames "
        "overlay each other perfectly. "
        # ── 6. Output ────────────────────────────────────────────────
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
