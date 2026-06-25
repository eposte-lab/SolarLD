"""Claude Vision service — estimates roof geometry from a satellite tile.

This is the fallback for points where `google_solar_service.fetch_building_insight`
returns `SolarApiNotFound` (Google has no aerial coverage, typically rural or
newly-built areas).

Flow:
  1. Hunter agent detects a 404 from Solar.
  2. Builds a Mapbox Static satellite URL at zoom=19 (~0.3m/px).
  3. Calls `estimate_roof_from_image(url, lat, lng)` here, which downloads the
     tile itself and sends Claude the bytes as base64 (NOT the URL — Anthropic's
     URL fetcher honours Mapbox's robots.txt and would be rejected with 400).
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
from dataclasses import dataclass

from anthropic import AsyncAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger
from . import mapbox_service
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
    image_block = await mapbox_service.fetch_image_base64_block(image_url)
    msg = await client.messages.create(
        model=model or settings.anthropic_model,
        max_tokens=512,
        temperature=0.0,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    image_block,
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


# ---------------------------------------------------------------------------
# Existing-PV detector — lead-quality gate (don't pitch solar to a roof that
# already went solar). Cheap (~0.5¢/call, image is fetched by Claude via URL).
# ---------------------------------------------------------------------------

_EXISTING_PV_SYSTEM = (
    "You are a remote-sensing specialist inspecting Italian aerial imagery of a "
    "company's site. Judge whether photovoltaic (solar) panels are ALREADY "
    "installed ANYWHERE on the property — on the building's roof, on "
    "carport/parking canopies, or ground-mounted within the site. A property "
    "with ANY existing PV — even a SMALL or PARTIAL array of just a few modules, "
    "covering only one section of the roof — has reduced energy need and is OUT "
    "of target: flag it. Do NOT require full-roof coverage; a single visible "
    "cluster of PV modules is enough. When a feature shows a panel-like grid of "
    "modules but you are unsure whether it is PV, lean toward TRUE — pitching "
    "solar to a roof that already has it is far worse than a false positive. "
    "(But a plain uniform dark/membrane roof with NO module pattern is not PV.)"
)

_EXISTING_PV_PROMPT = """\
Look at the main property in the central area of this satellite tile (latitude
{lat}, longitude {lng}) — the building together with its yard and car park.

Does this property ALREADY have solar photovoltaic panels installed anywhere on
site? Count PV regardless of where it sits AND regardless of how MUCH of the roof
it covers — even a SMALL or PARTIAL array (just a handful of modules on one
section) counts as YES:
  - on the building ROOFTOP (scan the WHOLE roof — panels often cover only one
    portion of it);
  - on CARPORT / parking canopies;
  - GROUND-MOUNTED within the property (in the yard or car park).
PV appears as a grid/rows of uniform dark blue/black rectangular modules; flag it
even if it is just one small cluster. Do NOT count: skylights, glass atriums,
sawtooth north-light roofs, plain uniform dark/membrane roofs (no module grid),
HVAC units, greenhouses, pools, parked vehicles, or a large off-site solar farm
clearly unrelated to this property.

Respond with EXACTLY this JSON (no prose, no code fences):
{{"has_existing_pv": boolean, "confidence": number, "notes": string}}
"""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15), reraise=True)
async def detect_existing_pv(
    image_url: str,
    lat: float,
    lng: float,
    *,
    model: str | None = None,
) -> dict[str, object] | None:
    """Claude-vision check: does the central rooftop ALREADY have PV panels?

    Returns ``{"has_existing_pv": bool, "confidence": float}`` or ``None`` on a
    parse/empty error. Callers must FAIL OPEN (treat None as "no existing PV")
    so a vision hiccup never silently rejects a good lead.
    """
    client = _get_client()
    image_block = await mapbox_service.fetch_image_base64_block(image_url)
    msg = await client.messages.create(
        model=model or settings.anthropic_model,
        max_tokens=200,
        temperature=0.0,
        system=_EXISTING_PV_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    image_block,
                    {"type": "text", "text": _EXISTING_PV_PROMPT.format(lat=lat, lng=lng)},
                ],
            }
        ],
    )
    text = ""
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            text = block.text  # type: ignore[attr-defined]
            break
    try:
        start, end = text.find("{"), text.rfind("}")
        parsed = json.loads(text[start : end + 1]) if start != -1 and end > start else None
    except (ValueError, json.JSONDecodeError):
        parsed = None
    if not isinstance(parsed, dict) or "has_existing_pv" not in parsed:
        return None
    return {
        "has_existing_pv": bool(parsed.get("has_existing_pv", False)),
        "confidence": float(parsed.get("confidence", 0.0) or 0.0),
    }


# Minimum vision confidence before we ACT on an existing-PV detection. Below
# this we keep the lead (a false reject costs lead supply; we only drop the
# clear cases like a roof fully tiled with panels).
EXISTING_PV_MIN_CONFIDENCE = 0.6


def _pv_zoom_for_area(area_sqm: float | None) -> int:
    """Pick the satellite zoom so the WHOLE property fits the tile.

    A fixed zoom-19 frame (~150 m) clips big ``capannoni`` and their car-park /
    ground PV arrays — the exact reason a 3000+ m² site with panels can read as
    "no PV". Zoom out for larger footprints so the roof AND the surrounding yard
    are visible.
    """
    if not area_sqm or area_sqm <= 0:
        return 19
    if area_sqm >= 6000:
        return 17  # ~600 m frame — sprawling sites + ground arrays
    if area_sqm >= 1500:
        return 18  # ~300 m frame — large warehouses + parking
    return 19


@dataclass(frozen=True)
class ExistingPvVerdict:
    """Tri-state existing-PV result for the FAIL-CLOSED gate.

    ``checked`` is True only when vision returned a verdict we can TRUST, i.e. at
    or above ``EXISTING_PV_MIN_CONFIDENCE``. When False the roof is UNVERIFIED
    (vision didn't run, timed out, was unparseable, or was too unsure) and the
    lead must be HELD — never marked ready_to_send, never emailed — until a
    confident verdict is obtained.

    Decision table:
      checked=True,  has_pv=True   -> HAS PANELS   -> reject / blacklist
      checked=True,  has_pv=False  -> VERIFIED CLEAN -> may proceed
      checked=False                -> UNVERIFIED   -> hold + re-verify
    """

    checked: bool
    has_pv: bool
    confidence: float


async def verify_existing_pv(
    lat: float, lng: float, *, area_sqm: float | None = None
) -> ExistingPvVerdict:
    """Full existing-PV verdict via a Mapbox satellite tile + Claude vision.

    Unlike :func:`building_has_existing_pv` (which collapses to a bool and is
    meant for FAIL-OPEN callers), this returns the tri-state so the funnel can
    FAIL CLOSED: only ``checked=True`` lets a lead proceed; a confident "no
    panels" is VERIFIED CLEAN, a confident "panels" is rejected, and anything
    else is UNVERIFIED and held.
    """
    zoom = _pv_zoom_for_area(area_sqm)
    try:
        url = mapbox_service.build_static_satellite_url(lat, lng, zoom=zoom, width=640, height=640)
    except Exception as exc:  # noqa: BLE001 — no token / build error
        log.warning("existing_pv.url_failed", err=str(exc)[:160])
        return ExistingPvVerdict(checked=False, has_pv=False, confidence=0.0)
    try:
        res = await detect_existing_pv(url, lat, lng)
    except Exception as exc:  # noqa: BLE001 — vision error
        log.warning("existing_pv.vision_failed", lat=lat, lng=lng, err=str(exc)[:160])
        return ExistingPvVerdict(checked=False, has_pv=False, confidence=0.0)
    if not res:
        return ExistingPvVerdict(checked=False, has_pv=False, confidence=0.0)
    confidence = float(res["confidence"])
    checked = confidence >= EXISTING_PV_MIN_CONFIDENCE
    has_pv = checked and bool(res["has_existing_pv"])
    log.info(
        "existing_pv.verified",
        lat=lat,
        lng=lng,
        zoom=zoom,
        area_sqm=area_sqm,
        has_existing_pv=res["has_existing_pv"],
        confidence=confidence,
        checked=checked,
        has_pv=has_pv,
    )
    return ExistingPvVerdict(checked=checked, has_pv=has_pv, confidence=confidence)


async def building_has_existing_pv(
    lat: float, lng: float, *, area_sqm: float | None = None
) -> bool | None:
    """Whether the property at ``(lat, lng)`` already has PV — bool|None facade
    over :func:`verify_existing_pv` for legacy FAIL-OPEN callers.

    Returns ``True`` only on a confident panels verdict, ``None`` when we could
    not confidently decide (no token, vision error, or below-confidence). Callers
    that gate leads on this must FAIL OPEN (treat None as "don't reject"); the
    funnel's fail-closed gate uses :func:`verify_existing_pv` instead.
    """
    verdict = await verify_existing_pv(lat, lng, area_sqm=area_sqm)
    if not verdict.checked:
        return None
    return verdict.has_pv
