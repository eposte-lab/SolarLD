"""Google Solar API client.

Wraps the Google Solar `buildingInsights:findClosest` endpoint which returns,
for a given lat/lng, the closest building polygon with:
  - roof area
  - max solar panel count / wattage / potential yearly kWh
  - per-segment azimuth + pitch + shading
  - center lat/lng and postal address

Docs: https://developers.google.com/maps/documentation/solar/building-insights

Costs (as of 2024): ~$0.02 per request on the `IMAGERY_AND_ALL_LAYERS` tier.
We cache 404s for 1h to avoid re-hammering empty coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

SOLAR_API_ENDPOINT = "https://solar.googleapis.com/v1/buildingInsights:findClosest"
# Per Google billing for solar; used for api_usage_log + scan_cost_cents.
COST_PER_CALL_CENTS = 2


class SolarApiError(Exception):
    """Non-retryable Solar API error."""


class SolarApiNotFound(Exception):
    """No building data available at this location (HTTP 404)."""


class SolarApiRateLimited(Exception):
    """Retryable 429 / 503."""


@dataclass(slots=True)
class RoofInsight:
    """Normalized projection of the Google Solar response.

    Only fields we care about for the Hunter pipeline are extracted.
    The full payload is preserved as `raw` for auditability / replay.
    """

    lat: float
    lng: float
    area_sqm: float
    estimated_kwp: float
    estimated_yearly_kwh: float
    max_panel_count: int
    panel_capacity_w: float
    dominant_exposure: str  # N/NE/E/SE/S/SW/W/NW
    pitch_degrees: float
    shading_score: float  # 0.0 = fully shaded, 1.0 = unobstructed
    postal_code: str | None
    region_code: str | None
    administrative_area: str | None
    locality: str | None
    raw: dict[str, Any]


def _azimuth_to_cardinal(deg: float) -> str:
    """Convert an azimuth in degrees (0=N, 90=E) to 8-point cardinal."""
    # Normalize into [0, 360)
    d = deg % 360.0
    # 8 sectors of 45°, offset by 22.5° so N covers [337.5, 22.5)
    points = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int(((d + 22.5) % 360.0) // 45.0)
    return points[idx]


def _parse_building_insights(data: dict[str, Any]) -> RoofInsight:
    """Project a `buildingInsights:findClosest` response to `RoofInsight`.

    Google returns `solarPotential` with a list of `roofSegmentStats` — each
    segment has its own azimuth/pitch/area. We pick the dominant segment by
    area, compute per-segment kWp as `maxPanels * panelCapacityWatts / 1000`.
    """
    center = data.get("center") or {}
    potential = data.get("solarPotential") or {}
    segments = potential.get("roofSegmentStats") or []

    # Fallback defaults if the segment list is empty
    dominant_azimuth = 180.0
    dominant_pitch = 20.0
    total_area = float(potential.get("wholeRoofStats", {}).get("areaMeters2", 0.0) or 0.0)
    shading = 1.0

    if segments:
        # Pick the segment with the largest area → dominant exposure
        dominant = max(segments, key=lambda s: float(s.get("stats", {}).get("areaMeters2", 0.0)))
        dominant_azimuth = float(dominant.get("azimuthDegrees", 180.0) or 180.0)
        dominant_pitch = float(dominant.get("pitchDegrees", 20.0) or 20.0)
        # Google gives "sunshineQuantiles" → mean of median buckets ≈ shading score
        quantiles = dominant.get("stats", {}).get("sunshineQuantiles") or []
        if quantiles:
            # Quantiles are listed as kWh/m²/year. Normalize against ~1600 kWh/m²/year peak.
            mean_sunshine = sum(quantiles) / len(quantiles)
            shading = max(0.0, min(1.0, mean_sunshine / 1600.0))

    max_panels = int(potential.get("maxArrayPanelsCount", 0) or 0)
    panel_w = float(potential.get("panelCapacityWatts", 0.0) or 0.0)
    estimated_kwp = (max_panels * panel_w) / 1000.0

    # Google lists multiple financial analyses; pick the first production total.
    yearly_kwh = 0.0
    if max_panels > 0 and potential.get("solarPanels"):
        # Sum yearly energy across default-count panel set
        yearly_kwh = sum(
            float(p.get("yearlyEnergyDcKwh", 0.0) or 0.0) for p in potential["solarPanels"]
        )
    if not yearly_kwh:
        # Fallback: typical Italian yield ≈ 1300 kWh/kWp
        yearly_kwh = estimated_kwp * 1300.0

    postal_addr = data.get("postalCode")  # Not always present
    # The Solar API sometimes returns `regionCode` + `administrativeArea` inline
    region_code = data.get("regionCode")
    admin_area = data.get("administrativeArea")
    locality = data.get("locality")

    return RoofInsight(
        lat=float(center.get("latitude", 0.0) or 0.0),
        lng=float(center.get("longitude", 0.0) or 0.0),
        area_sqm=round(total_area, 2),
        estimated_kwp=round(estimated_kwp, 2),
        estimated_yearly_kwh=round(yearly_kwh, 2),
        max_panel_count=max_panels,
        panel_capacity_w=panel_w,
        dominant_exposure=_azimuth_to_cardinal(dominant_azimuth),
        pitch_degrees=round(dominant_pitch, 2),
        shading_score=round(shading, 2),
        postal_code=postal_addr,
        region_code=region_code,
        administrative_area=admin_area,
        locality=locality,
        raw=data,
    )


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception_type(SolarApiRateLimited),
    reraise=True,
)
async def fetch_building_insight(
    lat: float,
    lng: float,
    *,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> RoofInsight:
    """Fetch the closest building insight to a point.

    Raises:
        SolarApiNotFound: no building detected at this coordinate.
        SolarApiError:    unrecoverable failure (bad key, quota exhausted, …).
    """
    key = api_key or settings.google_solar_api_key
    if not key:
        raise SolarApiError("GOOGLE_SOLAR_API_KEY not configured")

    params = {
        "location.latitude": f"{lat:.7f}",
        "location.longitude": f"{lng:.7f}",
        "requiredQuality": "HIGH",
        "key": key,
    }

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)

    try:
        resp = await client.get(SOLAR_API_ENDPOINT, params=params)
    finally:
        if owns_client:
            await client.aclose()

    if resp.status_code == 404:
        raise SolarApiNotFound(f"no building at ({lat}, {lng})")
    if resp.status_code in (429, 503):
        log.warning("solar_api_rate_limited", status=resp.status_code, lat=lat, lng=lng)
        raise SolarApiRateLimited(f"status={resp.status_code}")
    if resp.status_code >= 400:
        log.error("solar_api_error", status=resp.status_code, body=resp.text[:500])
        raise SolarApiError(f"status={resp.status_code} body={resp.text[:200]}")

    return _parse_building_insight_payload(resp.json())


def _parse_building_insight_payload(payload: dict[str, Any]) -> RoofInsight:
    """Public alias to allow unit tests to feed fixture JSON without an HTTP call."""
    return _parse_building_insights(payload)
