"""Claude Vision service — estimates roof geometry from a satellite tile.

This is the fallback for points where `google_solar_service.fetch_building_insight`
returns `SolarApiNotFound` (Google has no aerial coverage, typically rural or
newly-built areas).

Flow:
  1. Hunter agent detects a 404 from Solar.
  2. Builds a Mapbox Static satellite URL at zoom=19 (~0.3m/px).
  3. Calls `estimate_roof_from_image(url, lat, lng)` here.
  4. Claude returns a JSON object with `{has_building, area_sqm, exposure,
     pitch_degrees, shading_score}` which we project into a `RoofInsight`.

The accuracy is *deliberately conservative*: Claude is told to only respond
with `has_building=true` when it's ≥80% confident it sees a private or
commercial building (not a field, road, industrial shed without roof tiles,
or water). False positives pollute the roofs table with noise that Scoring
then rejects; false negatives are fine (the point is simply skipped).
"""

from __future__ import annotations

import json

from anthropic import AsyncAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger
from .google_solar_service import RoofInsight, _azimuth_to_cardinal

log = get_logger(__name__)

_client: AsyncAnthropic | None = None

# Claude Vision pricing for Sonnet 4.5 ≈ $3/M input, $15/M output. A typical
# call is ~800 input (image + prompt) + ~150 output tokens → ~$0.005 = 0.5¢.
VISION_COST_PER_CALL_CENTS = 1  # rounded up

SYSTEM_PROMPT = (
    "You are a remote-sensing specialist analysing Italian satellite imagery for "
    "rooftop photovoltaic potential. For each image you receive, respond with a "
    "strict JSON object matching the schema the user provides. Be CONSERVATIVE: "
    "when uncertain, set `has_building` to false."
)

USER_PROMPT_TEMPLATE = """\
Analyse this Mapbox satellite tile, centred at latitude {lat}, longitude {lng},
approximate ground resolution 0.3 m/pixel. The tile shows a 150m × 150m area.

Identify whether there is ONE well-defined building (private home, condominium,
commercial shed, or industrial warehouse) clearly visible in the central third
of the image. Ignore tents, cars, cranes, pools, solar farms on the ground, and
partially constructed buildings.

If yes, estimate:
  - roof area in square meters
  - dominant roof-ridge orientation as an azimuth in degrees
    (0 = North, 90 = East, 180 = South, 270 = West)
  - dominant pitch in degrees (5°–60°; use 5 for flat roofs)
  - sunshine availability score between 0.0 (heavy shade) and 1.0 (unobstructed)
    based on visible shadows in the tile
  - whether existing PV modules are already visible on the roof

Respond with EXACTLY this JSON object (no prose, no code fences):
{{
  "has_building": boolean,
  "confidence": number,
  "area_sqm": number,
  "azimuth_degrees": number,
  "pitch_degrees": number,
  "shading_score": number,
  "has_existing_pv": boolean,
  "notes": string
}}
"""


def _get_client() -> AsyncAnthropic:
    """Return a lazily-constructed Anthropic client."""
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured")
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15), reraise=True)
async def estimate_roof_from_image(
    image_url: str,
    lat: float,
    lng: float,
    *,
    model: str | None = None,
) -> RoofInsight | None:
    """Ask Claude to estimate roof geometry from a satellite tile URL.

    Returns `None` when Claude reports `has_building=false` or confidence < 0.8
    (caller should then treat the point as empty).
    """
    client = _get_client()
    msg = await client.messages.create(
        model=model or settings.anthropic_model,
        max_tokens=512,
        temperature=0.0,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "url", "url": image_url},
                    },
                    {
                        "type": "text",
                        "text": USER_PROMPT_TEMPLATE.format(lat=lat, lng=lng),
                    },
                ],
            }
        ],
    )

    text = ""
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            text = block.text  # type: ignore[attr-defined]
            break

    parsed = parse_vision_response(text)
    if parsed is None:
        log.warning("vision_parse_failed", raw=text[:400], lat=lat, lng=lng)
        return None

    return projection_to_insight(parsed, lat=lat, lng=lng, raw=parsed)


def parse_vision_response(text: str) -> dict[str, object] | None:
    """Parse a JSON response from Claude, tolerating accidental code fences.

    Returns `None` when the response can't be parsed or fails the sanity
    checks (missing fields, confidence too low, has_building=false).
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        # Strip ```json ... ``` fences
        stripped = stripped.split("```", 2)[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip().rstrip("`").strip()

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    has_building = bool(data.get("has_building", False))
    confidence = float(data.get("confidence", 0.0) or 0.0)
    if not has_building or confidence < 0.8:
        return None

    required = ["area_sqm", "azimuth_degrees", "pitch_degrees", "shading_score"]
    if any(k not in data for k in required):
        return None

    # Coerce + clamp
    try:
        data["area_sqm"] = float(data["area_sqm"])
        data["azimuth_degrees"] = float(data["azimuth_degrees"]) % 360.0
        data["pitch_degrees"] = max(0.0, min(90.0, float(data["pitch_degrees"])))
        data["shading_score"] = max(0.0, min(1.0, float(data["shading_score"])))
        data["has_existing_pv"] = bool(data.get("has_existing_pv", False))
        data["confidence"] = confidence
    except (TypeError, ValueError):
        return None

    return data


def projection_to_insight(
    data: dict[str, object],
    *,
    lat: float,
    lng: float,
    raw: dict[str, object] | None = None,
) -> RoofInsight:
    """Project a validated vision dict to a `RoofInsight`.

    Derived fields:
      - `estimated_kwp` ≈ `area_sqm × 0.17` (6 m²/kWp industry average for
        crystalline Si in 2024).
      - `estimated_yearly_kwh` ≈ `kwp × 1300 × shading_score` (Italian
        yield × shading penalty).
      - Panel count ≈ `kwp × 2.5` (400W panels).
    """
    area = float(data["area_sqm"])
    # Penalize vision-estimated areas by 15% — Claude tends to over-count footprint
    usable_area = area * 0.85
    kwp = round(usable_area / 6.0, 2)
    shading = float(data["shading_score"])
    kwh = round(kwp * 1300.0 * max(0.4, shading), 2)

    return RoofInsight(
        lat=lat,
        lng=lng,
        area_sqm=round(area, 2),
        estimated_kwp=kwp,
        estimated_yearly_kwh=kwh,
        max_panel_count=int(kwp * 2.5),
        panel_capacity_w=400.0,
        dominant_exposure=_azimuth_to_cardinal(float(data["azimuth_degrees"])),
        pitch_degrees=round(float(data["pitch_degrees"]), 2),
        shading_score=round(shading, 2),
        postal_code=None,
        region_code=None,
        administrative_area=None,
        locality=None,
        raw={"source": "claude_vision", **(raw or data)},
    )
