"""AI panel paint — replaces the PIL-geometric "after" frame with a
photorealistic edit produced by Google Gemini 2.5 Flash Image (served
via Replicate's ``google/nano-banana`` model).

Why this service exists
-----------------------

The previous pipeline drew the "after" frame programmatically:
``ImageDraw.polygon`` rectangles at exact lat/lng coords from the
Google Solar API, with a hand-crafted blue-cell texture. Output looked
"pasted on" — flat panels, no perspective with the roof tiles, no
ambient shadows, occasional "cubic dome" artifacts when the roof
segment wasn't a clean rectangle. Worse, that fake frame was fed to
Kling 1.6-Pro as ``end_image``, which forced the video model to
animate a panel-by-panel reveal converging on a fake end state — so
the GIF inherited the same wrongness.

The new flow:

    Google Solar API   →   panel_count, kwp, primary_azimuth   (DATA only)
                            ↓
    Real aerial GeoTIFF crop (1536²)   →  before_url
                            ↓
    Gemini Flash Image instruction-edit on the real photo
        prompt: "add ~N photorealistic monocrystalline panels to the
                 visible roof, preserve everything else …"
                            ↓
    after_url (real photo with photoreal panels)
                            ↓
    Kling 1.6-Pro (start=before, end=after, prompt=ambient timelapse)
                            ↓
    MP4 + GIF (with ROI overlay last 2 s)

Why ``google/nano-banana`` and not FLUX / SDXL
----------------------------------------------

* Instruction-following: nano-banana is calibrated for "do exactly this
  edit, leave everything else alone" prompts. SDXL img2img tends to
  re-imagine the entire scene with the prompt strength we'd need.
* Multi-image input support: lets us pass a reference panel close-up
  later if we want to lock the panel style further.
* Cost: ~$0.039 per image, wall-clock ~5-12 s. Fine for an async agent.
* No mask required: writes only where the prompt says to, so we avoid
  having to compute a per-roof binary mask (which was the next
  fall-back if we'd gone with FLUX-Fill).

Override points (env)
---------------------

``REPLICATE_API_TOKEN``        — required
``AI_PAINT_MODEL_OWNER``       — defaults to ``google``
``AI_PAINT_MODEL_NAME``        — defaults to ``nano-banana``
                                 (set to e.g. ``black-forest-labs/flux-kontext-pro``
                                 to swap engines without code changes)

Failure mode
------------

Raises :class:`AiPaintError` on any unrecoverable failure. The caller
(``creative.CreativeAgent``) treats this as non-fatal: it skips the
rendering for that lead and continues with the ROI computation, so the
outreach doesn't grind to a halt because Replicate is having a bad
afternoon.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)


REPLICATE_API_BASE = "https://api.replicate.com/v1"

# Defaults are env-overridable so we can A/B engines without redeploys.
DEFAULT_MODEL_OWNER = os.getenv("AI_PAINT_MODEL_OWNER", "google")
DEFAULT_MODEL_NAME = os.getenv("AI_PAINT_MODEL_NAME", "nano-banana")

# nano-banana publishes "predictions" via the model-slug endpoint, which
# auto-resolves the latest version. We keep this format so swapping the
# slug above doesn't require chasing a version hash.
_PREDICTIONS_URL_FMT = (
    REPLICATE_API_BASE + "/models/{owner}/{name}/predictions"
)
_PREDICTION_URL_FMT = REPLICATE_API_BASE + "/predictions/{pred_id}"


class AiPaintError(Exception):
    """Raised when the AI paint call fails to return a usable image."""


# ---------------------------------------------------------------------------
# Prompt construction (pure)
# ---------------------------------------------------------------------------


def _azimuth_to_compass(deg: float) -> str:
    """Translate a 0-360° azimuth to plain English compass direction."""
    deg = deg % 360
    if 22.5 <= deg < 67.5:
        return "northeast"
    if 67.5 <= deg < 112.5:
        return "east"
    if 112.5 <= deg < 157.5:
        return "southeast"
    if 157.5 <= deg < 202.5:
        return "south"
    if 202.5 <= deg < 247.5:
        return "southwest"
    if 247.5 <= deg < 292.5:
        return "west"
    if 292.5 <= deg < 337.5:
        return "northwest"
    return "north"


def build_paint_prompt(
    *,
    panel_count: int,
    primary_azimuth_deg: float | None = None,
    kwp: float | None = None,
    subject_type: str = "unknown",
    roof_area_sqm: float | None = None,
    roof_segment_count: int | None = None,
    roof_pitch_deg: float | None = None,
) -> str:
    """Compose the master instruction Gemini Flash Image will follow.

    The prompt is intentionally specific:
      1. ROOF IDENTIFICATION first — explicit description of WHAT the
         rooftop is so the model doesn't pick a courtyard, awning or
         neighbouring lot. The image is already cropped on the target
         building (Solar API centroid) but nano-banana still needs the
         text instruction to lock onto the roof, especially for
         L-shaped or terraced houses where multiple candidates exist.
      2. STRICT BOUNDARIES — explicit, exhaustive list of what is NOT
         a valid panel surface (grass, lawn, courtyard, driveway,
         road, parking, sidewalk, neighbouring building, awning,
         canopy, solar shade, terrace, balcony, ground). Production
         feedback: panels were ending up on the lawn or driveway —
         the previous prompt only said "preserve everything else"
         which apparently isn't strict enough for nano-banana.
      3. "approximately N panels" — Solar API's count, prevents the
         model from over-filling like SDXL did.
      4. compass direction — guides placement to the correct roof
         segments on L-shaped buildings.
      5. "lie flat on roof, follow perspective" — kills the cube /
         floating-dome artefacts the old PIL render produced.
      6. ABORT clause — if no clear rooftop is visible, return the
         image unchanged rather than inventing one. nano-banana
         honours this; SDXL would not.
    """
    panel_word = (
        f"approximately {panel_count}" if panel_count > 0 else "several"
    )

    type_hint = {
        "b2b": "commercial or industrial building",
        "b2c": "private residential house",
    }.get(subject_type.lower(), "building")

    azimuth_hint = ""
    if primary_azimuth_deg is not None:
        compass = _azimuth_to_compass(primary_azimuth_deg)
        azimuth_hint = (
            f" Place the panels primarily on the {compass}-facing roof "
            "segments, since that is where the structural analysis "
            "identified the most suitable surface."
        )

    scale_hint = (
        f", forming roughly a {kwp:.0f} kWp photovoltaic array"
        if kwp and kwp > 0
        else ""
    )

    # Roof geometry hints sourced from Google Solar API. These are the
    # tightest constraints we have on what the rooftop *actually* looks
    # like and they massively reduce the chance nano-banana paints panels
    # on a neighbouring building when the aerial crop has overlap. The
    # numbers are appended into a single sentence so the model treats them
    # as constraints rather than separate (and skippable) bullet points.
    geometry_parts: list[str] = []
    if roof_area_sqm and roof_area_sqm > 0:
        geometry_parts.append(
            f"approximately {roof_area_sqm:.0f} square metres of usable roof surface"
        )
    if roof_segment_count and roof_segment_count > 0:
        plane_word = "plane" if roof_segment_count == 1 else "distinct planes"
        geometry_parts.append(
            f"{roof_segment_count} {plane_word}"
        )
    if roof_pitch_deg is not None and roof_pitch_deg > 0:
        geometry_parts.append(
            f"a dominant pitch of about {roof_pitch_deg:.0f}°"
        )
    geometry_hint = ""
    if geometry_parts:
        geometry_hint = (
            " The structural analysis identified "
            + ", ".join(geometry_parts)
            + " on this rooftop — use these dimensions as a hard constraint "
            "for panel placement and total array footprint, and never let "
            "the array spill onto adjacent surfaces."
        )

    return (
        # ── 1. Identify the target ────────────────────────────────────
        "TASK: Edit this top-down aerial photograph by adding "
        "photorealistic monocrystalline silicon solar panels onto the "
        "rooftop of the principal building visible at the centre of "
        "the image. "
        f"The principal building is the {type_hint} occupying the "
        "centre of the frame; identify its rooftop as the contiguous "
        "elevated surface bounded by the building's outer walls — "
        "typically recognisable by its roof tiles, sheet metal, "
        "shingles, or flat membrane material, and visibly raised "
        "above the surrounding ground level. "
        # ── 2. Lock-down: where panels must NOT go ───────────────────
        "STRICT BOUNDARIES — panels MUST be placed EXCLUSIVELY within "
        "the perimeter of that rooftop. Under NO circumstances place "
        "panels on: grass, lawn, garden, courtyard, driveway, "
        "parking lot, paved ground, sidewalk, road, street, "
        "neighbouring buildings, balconies, terraces, awnings, "
        "canopies, pergolas, swimming pools, or any surface that is "
        "not the roof of the principal building described above. "
        "If a portion of a row would extend past the roof edge, "
        "shorten the row instead — never let a panel hang over open "
        "ground. "
        # ── 3. Quantity, layout, scale ───────────────────────────────
        f"Add {panel_word} dark-blue rectangular panels with thin "
        "silver aluminium frames, arranged in clean parallel rows "
        f"aligned with the existing roof edges{scale_hint}."
        f"{azimuth_hint}"
        f"{geometry_hint} "
        # ── 4. Geometry / lighting ──────────────────────────────────
        "The panels MUST lie flat on the roof surface, follow its "
        "exact perspective, slope and orientation, and inherit the "
        "same daylight direction and shadow softness as the rest of "
        "the scene. Add subtle ambient-occlusion shadows where the "
        "panels meet the roof. Maintain a uniform inter-panel gap of "
        "roughly 2 cm, and a minimum 30 cm clear setback from any "
        "roof edge, ridge, valley, chimney or skylight. "
        # ── 5. Preserve everything else ──────────────────────────────
        "Do NOT modify, recolour, blur, or relight any pixel outside "
        "the rooftop — ground, vegetation, vehicles, roads, "
        "neighbouring buildings, shadows on the ground all stay "
        "EXACTLY as they appear in the source image. "
        "Do NOT add text, logos, watermarks, captions or arrows. "
        # ── 6. Abort clause ──────────────────────────────────────────
        "If you cannot confidently identify a rooftop suitable for "
        "panels in this image, return the image unchanged rather than "
        "guessing. "
        # ── 7. Output style ──────────────────────────────────────────
        "Output style: top-down aerial perspective, sharp focus, "
        "professional real-estate photography quality, natural "
        "daylight, no artistic filters."
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
async def paint_panels_on_aerial(
    *,
    before_image_url: str,
    panel_count: int,
    primary_azimuth_deg: float | None = None,
    kwp: float | None = None,
    subject_type: str = "unknown",
    roof_area_sqm: float | None = None,
    roof_segment_count: int | None = None,
    roof_pitch_deg: float | None = None,
    poll_timeout_s: float = 120.0,
    poll_interval_s: float = 2.0,
    http_client: httpx.AsyncClient | None = None,
    model_owner: str | None = None,
    model_name: str | None = None,
) -> bytes:
    """Run AI panel painting on the before image, return AFTER PNG bytes.

    Synchronous from the caller's POV: creates the prediction, polls
    until done, downloads the resulting PNG, returns the raw bytes.
    The caller is responsible for uploading the bytes to Supabase
    Storage (so the function stays storage-backend-agnostic and unit
    testable without Supabase).

    Tenacity wraps the whole call with up to 3 attempts: Replicate's
    A40 shared queue occasionally OOMs on a cold-start prediction, and
    a second attempt usually lands on a warmed worker.
    """
    owner = model_owner or DEFAULT_MODEL_OWNER
    name = model_name or DEFAULT_MODEL_NAME
    create_url = _PREDICTIONS_URL_FMT.format(owner=owner, name=name)

    prompt = build_paint_prompt(
        panel_count=panel_count,
        primary_azimuth_deg=primary_azimuth_deg,
        kwp=kwp,
        subject_type=subject_type,
        roof_area_sqm=roof_area_sqm,
        roof_segment_count=roof_segment_count,
        roof_pitch_deg=roof_pitch_deg,
    )

    log.info(
        "ai_paint.start",
        before_url_peek=before_image_url[:100],
        prompt_peek=prompt[:140],
        panel_count=panel_count,
        kwp=kwp,
        model=f"{owner}/{name}",
    )

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=60.0)
    )

    try:
        # 1) Create prediction. ``Prefer: wait=10`` lets Replicate hold
        #    the connection open for up to 10 s and return the result
        #    inline if the model finishes by then — saves one poll round
        #    trip on the happy path (nano-banana typically completes in
        #    5-8 s).
        resp = await client.post(
            create_url,
            headers={**_auth_headers(), "Prefer": "wait=10"},
            json={
                "input": {
                    "prompt": prompt,
                    "image_input": [before_image_url],
                    "output_format": "png",
                }
            },
        )
        # 429 = rate-limit. Replicate's `Retry-After` header tells us how
        # many seconds to back off; if absent we fall back to 30 s. Sleep
        # in-process and retry once instead of failing the whole render —
        # tenacity's exponential backoff caps at 10 s which is too short
        # when the per-account rate limit has been reduced (typically the
        # 6/min burst-1 throttle for accounts without payment method).
        if resp.status_code == 429:
            retry_after_s = 30.0
            ra_header = resp.headers.get("Retry-After")
            if ra_header:
                try:
                    retry_after_s = float(ra_header)
                except ValueError:
                    pass
            log.warning(
                "ai_paint.rate_limited",
                retry_after_s=retry_after_s,
                body_peek=resp.text[:200],
            )
            await asyncio.sleep(min(retry_after_s, 60.0))
            resp = await client.post(
                create_url,
                headers={**_auth_headers(), "Prefer": "wait=10"},
                json={
                    "input": {
                        "prompt": prompt,
                        "image_input": [before_image_url],
                        "output_format": "png",
                    }
                },
            )
        if resp.status_code >= 400:
            raise AiPaintError(
                f"create status={resp.status_code} body={resp.text[:300]}"
            )
        body = resp.json()
        pred_id = body.get("id")
        status = body.get("status", "")
        output = body.get("output")
        error = body.get("error")

        # 2) Poll if not synchronous-finished.
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
                raise AiPaintError(
                    f"poll {pred_id} status={r.status_code} body={r.text[:200]}"
                )
            j = r.json()
            status = j.get("status", "")
            output = j.get("output")
            error = j.get("error")

        if status != "succeeded":
            raise AiPaintError(
                f"prediction {pred_id} ended {status}: {error or 'unknown'}"
            )

        out_url = _extract_output_url(output)
        if not out_url:
            raise AiPaintError(f"no output URL in response (output={output!r})")

        # 3) Download the produced PNG. Replicate URLs expire after ~1 h
        #    so we must materialise bytes in the same call window.
        dl = await client.get(out_url, timeout=60.0)
        if dl.status_code != 200:
            raise AiPaintError(
                f"download {out_url[:80]} status={dl.status_code}"
            )
        png_bytes = dl.content

        log.info(
            "ai_paint.done",
            prediction_id=pred_id,
            output_kb=len(png_bytes) // 1024,
        )
        return png_bytes

    finally:
        if owns_client:
            await client.aclose()
