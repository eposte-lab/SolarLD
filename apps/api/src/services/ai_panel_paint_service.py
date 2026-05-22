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

    Hard-learned design notes (every line is a previous failure):

    * Long prompts dilute. The model loses focus and starts inventing.
      We keep it short and rule-numbered so each constraint gets equal
      weight. Earlier rules carry more weight in LLM attention → SCALE
      and PIXEL-PRESERVATION go first.
    * Never give the model "permission to skip" (previous version said
      "if you cannot mount a panel without altering the roof, leave
      that spot bare" → it left HALF the roof bare). Coverage must be
      framed as a hard requirement.
    * Without an explicit physical scale, the model paints panels as
      large as cars. Anchor the size to objects naturally visible in
      aerial photos (cars, parking lines) so the model has a ground
      truth to measure against.
    * Pixel preservation must apply to the ENTIRE photo, not just the
      roof. Otherwise the model "improves" cars, parking lines, the
      surrounding context — and the before→after crossfade looks like
      two different photos.
    """
    count = f"~{panel_count} " if panel_count > 0 else ""
    scale = f" forming a roughly {kwp:.0f} kWp array" if kwp and kwp > 0 else ""
    return (
        "TASK: in this top-down aerial photo, add photorealistic solar "
        "panels on top of the main central building's rooftop. Do "
        "nothing else.\n\n"
        # ── Rule 1: PIXEL PRESERVATION (everything, not just roof) ──
        "RULE 1 — PRESERVE EVERY PIXEL EXCEPT WHERE A PANEL COVERS IT. "
        "The output must be IDENTICAL to the input everywhere except "
        "where you place a panel. The roof's existing surface (tiles, "
        "sheets, gravel, membrane) underneath any uncovered area must "
        "stay exactly as in the input. Cars, parking lines, trees, "
        "roads, sidewalks, neighbouring buildings, the building's "
        "walls and outline — none of these may be modified, "
        "re-imagined, recoloured, repainted or shifted by even one "
        "pixel. Do not invent objects that aren't there. Do not "
        "introduce roof tiles or coverings the input doesn't show. "
        "Treat the input as a fixed background. The ONLY permitted "
        "change is adding panels.\n\n"
        # ── Rule 2: SCALE (panels are SMALL) ────────────────────────
        "RULE 2 — PANEL SCALE: a single panel is ~1.65 m × 1 m in the "
        "real world. That is HALF the length of a parked car and "
        "HALF the width of a single parking space. Use cars and "
        "parking-line markings visible in the photo as your scale "
        "reference. A single panel painted as wide as a car or wider "
        "is WRONG. Always paint many small panels in tight rows, "
        "never a few oversized ones.\n\n"
        # ── Rule 3: COVERAGE (full, not partial) ─────────────────────
        f"RULE 3 — COVER THE WHOLE SUITABLE ROOF. Add {count}panels"
        f"{scale} in continuous, neat rows over EVERY usable roof "
        "surface of the central main building. On sloped faces the "
        "rows lie flat against each pitch and follow its slope. On "
        "flat roofs lay parallel rows across the area. Leave only "
        "the minimum required gaps: a slim margin at the eaves and "
        "verges, the ridges/hips themselves, tight cut-outs around "
        "chimneys, skylights, HVAC units and vents. The installation "
        "must read as FULL coverage of the roof, not as a few "
        "scattered patches.\n\n"
        # ── Rule 4: BOUNDARIES ──────────────────────────────────────
        "RULE 4 — BOUNDARIES. Panels go ONLY on the main central "
        "building's rooftop. Never on the ground, parking, road, "
        "garden, courtyard, or any neighbouring building. If a row "
        "reaches a roof edge, shorten it rather than overhang.\n\n"
        # ── Rule 5: FRAMING LOCK ─────────────────────────────────────
        "RULE 5 — FRAMING LOCK. Same pixel dimensions, same crop, "
        "same zoom, same camera position as the input. No pan, no "
        "rotation, no re-crop, no scale change.\n\n"
        # ── Style ────────────────────────────────────────────────────
        "STYLE — Modern monocrystalline silicon: near-black / very "
        "dark blue, thin silver aluminium frame, faint cell grid, "
        "soft realistic shadows where each row meets the roof, sun-"
        "consistent specular sheen. The array looks photographed, "
        "not pasted.\n\n"
        "OUTPUT — photorealistic, top-down aerial, sharp focus, no "
        "text, no watermarks."
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
