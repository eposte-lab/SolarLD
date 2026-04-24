"""Google Solar API client.

Wraps two Google Solar endpoints:

1. `buildingInsights:findClosest` — roof area, kWp, per-panel geometry.
2. `dataLayers:get?view=IMAGERY_LAYERS` — aerial RGB GeoTIFF at ~10 cm/pixel.

The `fetch_building_insight` call is used in the Hunter L4 funnel gate.
The `fetch_data_layers` call is used by the Creative agent to obtain the
high-quality "before" image for the before/after rendering pipeline.

Docs:
  https://developers.google.com/maps/documentation/solar/building-insights
  https://developers.google.com/maps/documentation/solar/data-layers

Costs (as of 2024):
  buildingInsights: ~$0.02/request
  dataLayers:       ~$0.03/request
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

SOLAR_API_ENDPOINT = "https://solar.googleapis.com/v1/buildingInsights:findClosest"
SOLAR_DATA_LAYERS_ENDPOINT = "https://solar.googleapis.com/v1/dataLayers:get"
SOLAR_GEOTIFF_ENDPOINT = "https://solar.googleapis.com/v1/geoTiff:get"
# Per Google billing for solar; used for api_usage_log + scan_cost_cents.
COST_PER_CALL_CENTS = 2
COST_DATA_LAYERS_CENTS = 3  # dataLayers:get (IMAGERY_LAYERS view)


class SolarApiError(Exception):
    """Non-retryable Solar API error."""


class SolarApiNotFound(Exception):
    """No building data available at this location (HTTP 404)."""


class SolarApiRateLimited(Exception):
    """Retryable 429 / 503."""


@dataclass(slots=True)
class SolarPanel:
    """One panel in the Google Solar optimal layout.

    `segment_azimuth_deg` is resolved from the parent segment so callers
    don't need to look it up separately.
    """

    lat: float
    lng: float
    orientation: str          # "LANDSCAPE" | "PORTRAIT"
    segment_azimuth_deg: float
    yearly_energy_kwh: float
    segment_index: int


@dataclass(slots=True)
class DataLayers:
    """Minimal projection of the `dataLayers:get` response.

    The `rgb_url` is the base URL of the GeoTIFF.  To download it you
    must append ``?key={api_key}`` — the key is not baked in to avoid
    leaking it into logs.
    """

    rgb_url: str
    imagery_quality: str      # "HIGH" | "MEDIUM" | "LOW"
    imagery_date: str         # "YYYY-MM-DD" (best effort)


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
    # Panel geometry — populated when solarPotential.solarPanels is present.
    panels: list[SolarPanel] = field(default_factory=list)
    panel_width_m: float = 1.045   # Google Solar default (standard 2024 module)
    panel_height_m: float = 1.879


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
    raw_panels: list[dict[str, Any]] = potential.get("solarPanels") or []
    if raw_panels:
        yearly_kwh = sum(float(p.get("yearlyEnergyDcKwh", 0.0) or 0.0) for p in raw_panels)
    if not yearly_kwh:
        # Fallback: typical Italian yield ≈ 1300 kWh/kWp
        yearly_kwh = estimated_kwp * 1300.0

    # Build a fast segment-index → azimuth lookup.
    seg_azimuths: dict[int, float] = {}
    for i, seg in enumerate(segments):
        seg_azimuths[i] = float(seg.get("azimuthDegrees", 180.0) or 180.0)

    # Panel physical dimensions (Google provides these per-potential block).
    panel_width_m = float(potential.get("panelWidthMeters", 1.045) or 1.045)
    panel_height_m = float(potential.get("panelHeightMeters", 1.879) or 1.879)

    panels: list[SolarPanel] = []
    for p in raw_panels:
        p_center = p.get("center") or {}
        p_lat = float(p_center.get("latitude", 0.0) or 0.0)
        p_lng = float(p_center.get("longitude", 0.0) or 0.0)
        if not p_lat and not p_lng:
            continue
        seg_idx = int(p.get("segmentIndex", 0) or 0)
        panels.append(
            SolarPanel(
                lat=p_lat,
                lng=p_lng,
                orientation=str(p.get("orientation", "LANDSCAPE")),
                segment_azimuth_deg=seg_azimuths.get(seg_idx, dominant_azimuth),
                yearly_energy_kwh=float(p.get("yearlyEnergyDcKwh", 0.0) or 0.0),
                segment_index=seg_idx,
            )
        )

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
        panels=panels,
        panel_width_m=panel_width_m,
        panel_height_m=panel_height_m,
    )


def _mock_roof_insight(lat: float, lng: float) -> RoofInsight:
    """Generate a deterministic synthetic RoofInsight for testing.

    Seeded on (lat, lng) so the same coordinate always returns the same
    roof — idempotent re-runs won't oscillate between pass/fail filter.

    Output ranges are calibrated for Italian commercial B2B roofs:
      - Area 200–2 000 m²
      - kWp  20–200 kWp  (panel_w=400 W, typical SME install)
      - Yield 1 200–1 450 kWh/kWp  (Italian sun average)
      - Shading 0.70–1.00
      - Exposure: S / SE / SW / E / W  (never N → passes filter)
    """
    # Two independent MD5 hashes for independent variation across fields.
    seed_a = f"solar_mock_a_{lat:.5f}_{lng:.5f}".encode()
    seed_b = f"solar_mock_b_{lat:.5f}_{lng:.5f}".encode()
    ha = int(hashlib.md5(seed_a).hexdigest(), 16)
    hb = int(hashlib.md5(seed_b).hexdigest(), 16)

    # Area: 200–2000 m²  (200 + [0,1800])
    area_sqm = 200.0 + (ha % 1801)
    # Panel count: drives kWp
    panel_w = 400.0  # Watt-peak per panel (typical 2024 module)
    # Panels: 50–500  → 20–200 kWp
    n_panels = 50 + (hb % 451)
    estimated_kwp = round((n_panels * panel_w) / 1000.0, 2)
    # Italian yield 1200–1450 kWh/kWp
    yield_factor = 1200.0 + ((ha >> 16) % 251)
    estimated_yearly_kwh = round(estimated_kwp * yield_factor, 2)
    # Shading 0.70–1.00
    shading_score = round(0.70 + ((hb >> 16) % 31) / 100.0, 2)
    # Pitch 15–35°
    pitch_degrees = round(15.0 + (ha % 21), 2)
    # Exposure: never N — pick from good Italian azimuths
    good_exposures = ["S", "SE", "SW", "E", "W"]
    dominant_exposure = good_exposures[hb % len(good_exposures)]

    raw: dict[str, Any] = {
        "_mock": True,
        "seed_lat": lat,
        "seed_lng": lng,
    }
    log.debug(
        "solar_mock_roof_generated",
        extra={
            "lat": lat,
            "lng": lng,
            "area_sqm": area_sqm,
            "estimated_kwp": estimated_kwp,
            "exposure": dominant_exposure,
        },
    )
    # Generate a simple grid of mock panels around the building center.
    # Each panel is 1.045 × 1.879 m; panels are placed on a 2m grid,
    # facing South (azimuth=180°) — the dominant Italian exposure.
    mock_panel_w = 1.045
    mock_panel_h = 1.879
    deg_per_m_lat = 1 / 111320.0
    deg_per_m_lng = 1 / (111320.0 * 0.94)  # cos(20°) ≈ Italy mid-latitude
    mock_panels: list[SolarPanel] = []
    cols = max(1, int((area_sqm ** 0.5) / 2.0))
    rows = max(1, n_panels // max(1, cols))
    for ri in range(rows):
        for ci in range(cols):
            if len(mock_panels) >= n_panels:
                break
            p_lat = lat + (ri - rows / 2) * mock_panel_h * deg_per_m_lat
            p_lng = lng + (ci - cols / 2) * mock_panel_w * deg_per_m_lng
            mock_panels.append(
                SolarPanel(
                    lat=p_lat,
                    lng=p_lng,
                    orientation="LANDSCAPE",
                    segment_azimuth_deg=180.0,
                    yearly_energy_kwh=float(estimated_yearly_kwh) / max(1, n_panels),
                    segment_index=0,
                )
            )

    return RoofInsight(
        lat=lat,
        lng=lng,
        area_sqm=round(area_sqm, 2),
        estimated_kwp=estimated_kwp,
        estimated_yearly_kwh=estimated_yearly_kwh,
        max_panel_count=n_panels,
        panel_capacity_w=panel_w,
        dominant_exposure=dominant_exposure,
        pitch_degrees=pitch_degrees,
        shading_score=shading_score,
        postal_code=None,
        region_code="IT",
        administrative_area=None,
        locality=None,
        raw=raw,
        panels=mock_panels,
        panel_width_m=mock_panel_w,
        panel_height_m=mock_panel_h,
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
    effective_key = api_key or settings.google_solar_api_key

    # ── Mock mode ─────────────────────────────────────────────────────────────
    # Fires when GOOGLE_SOLAR_MOCK_MODE=true AND no real key is configured.
    # A real key always takes priority — consistent with the Atoka mock pattern.
    # Generates deterministic but plausible synthetic RoofInsight data so the
    # full L4 pipeline can be exercised (Solar→filter→upsert roofs+subjects)
    # without consuming API quota.
    #
    # Values are seeded on (lat, lng) so the same coordinate always returns
    # the same roof — idempotent re-runs won't oscillate between pass/fail.
    # Generated values are all non-zero and dominant_exposure is never "N",
    # so they pass the default TechnicalFilters thresholds.
    if settings.google_solar_mock_mode and not effective_key:
        return _mock_roof_insight(lat, lng)

    key = effective_key
    if not key:
        raise SolarApiError("GOOGLE_SOLAR_API_KEY not configured")

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)

    # Quality tier-down: HIGH → MEDIUM → LOW with expanding radius.
    # Italian coverage outside major metro cores is mostly MEDIUM/LOW.
    # Using requiredQuality=HIGH alone misses ~65% of valid roofs in Italy.
    # radiusMeters=150 lets the API find a building when the geocoded point
    # (typically on the street) is offset from the actual roof centroid.
    quality_tiers = [
        ("HIGH",   "100"),
        ("MEDIUM", "150"),
        ("LOW",    "150"),
    ]

    try:
        for quality, radius in quality_tiers:
            params = {
                "location.latitude": f"{lat:.7f}",
                "location.longitude": f"{lng:.7f}",
                "requiredQuality": quality,
                "radiusMeters": radius,
                "key": key,
            }
            resp = await client.get(SOLAR_API_ENDPOINT, params=params)

            if resp.status_code == 200:
                if quality != "HIGH":
                    log.info(
                        "solar_api_quality_tier_down",
                        lat=lat, lng=lng, quality=quality,
                    )
                return _parse_building_insight_payload(resp.json())

            if resp.status_code == 404:
                # Nothing at this quality — try next tier
                continue

            if resp.status_code in (429, 503):
                log.warning(
                    "solar_api_rate_limited", status=resp.status_code,
                    lat=lat, lng=lng,
                )
                raise SolarApiRateLimited(f"status={resp.status_code}")

            # Any other 4xx/5xx is a hard error — stop immediately
            log.error("solar_api_error", status=resp.status_code, body=resp.text[:500])
            raise SolarApiError(f"status={resp.status_code} body={resp.text[:200]}")

        # Exhausted all quality tiers with 404 each time
        raise SolarApiNotFound(f"no building at ({lat}, {lng})")

    finally:
        if owns_client:
            await client.aclose()


def _parse_building_insight_payload(payload: dict[str, Any]) -> RoofInsight:
    """Public alias to allow unit tests to feed fixture JSON without an HTTP call."""
    return _parse_building_insights(payload)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(SolarApiRateLimited),
    reraise=True,
)
async def fetch_data_layers(
    lat: float,
    lng: float,
    *,
    radius_m: int = 40,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> DataLayers:
    """Fetch aerial RGB imagery metadata for a point.

    Returns a ``DataLayers`` with ``rgb_url`` pointing at the GeoTIFF.
    The caller is responsible for downloading the GeoTIFF separately
    (append ``?key={api_key}`` to the URL).

    Raises:
        SolarApiNotFound: no imagery at this location.
        SolarApiError:    unrecoverable failure.
    """
    effective_key = api_key or settings.google_solar_api_key
    if not effective_key:
        raise SolarApiError("GOOGLE_SOLAR_API_KEY not configured")

    # Google Solar dataLayers `view` enum (IMAGERY_ONLY does NOT exist —
    # using it returns HTTP 400 "Invalid value at 'view'"). Valid values:
    # DSM_LAYER | IMAGERY_LAYERS | IMAGERY_AND_ANNUAL_FLUX_LAYERS |
    # IMAGERY_AND_ALL_FLUX_LAYERS | FULL_LAYERS.  We only need the RGB
    # aerial, so IMAGERY_LAYERS is the cheapest option.
    params = {
        "location.latitude": f"{lat:.7f}",
        "location.longitude": f"{lng:.7f}",
        "radiusMeters": str(radius_m),
        "view": "IMAGERY_LAYERS",
        "requiredQuality": "HIGH",
        "key": effective_key,
    }

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=20.0)

    try:
        resp = await client.get(SOLAR_DATA_LAYERS_ENDPOINT, params=params)
    finally:
        if owns_client:
            await client.aclose()

    if resp.status_code == 404:
        raise SolarApiNotFound(f"no data layers at ({lat}, {lng})")
    if resp.status_code in (429, 503):
        log.warning("solar_data_layers_rate_limited", status=resp.status_code, lat=lat, lng=lng)
        raise SolarApiRateLimited(f"status={resp.status_code}")
    if resp.status_code >= 400:
        log.error("solar_data_layers_error", status=resp.status_code, body=resp.text[:300])
        raise SolarApiError(f"dataLayers status={resp.status_code} body={resp.text[:200]}")

    data = resp.json()
    rgb_url = data.get("rgbUrl", "")
    if not rgb_url:
        raise SolarApiError("dataLayers response missing rgbUrl")

    # Normalise imagery date from {"year": 2022, "month": 8, "day": 15}
    d = data.get("imageryDate") or {}
    imagery_date = (
        f"{d.get('year', '?')}-{d.get('month', '?'):02d}-{d.get('day', '?'):02d}"
        if d
        else "unknown"
    )

    return DataLayers(
        rgb_url=rgb_url,
        imagery_quality=str(data.get("imageryQuality", "UNKNOWN")),
        imagery_date=imagery_date,
    )


async def download_geotiff(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> bytes:
    """Download a GeoTIFF from a Solar API URL (appends the API key).

    The ``url`` comes from ``DataLayers.rgb_url`` which is a base URL
    without the key; we append it here so the key never appears in logs.
    """
    effective_key = api_key or settings.google_solar_api_key
    if not effective_key:
        raise SolarApiError("GOOGLE_SOLAR_API_KEY not configured for GeoTIFF download")

    full_url = f"{url}&key={effective_key}" if "?" in url else f"{url}?key={effective_key}"

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=60.0)

    try:
        resp = await client.get(full_url)
        if resp.status_code >= 400:
            raise SolarApiError(f"GeoTIFF download failed: status={resp.status_code}")
        return resp.content
    finally:
        if owns_client:
            await client.aclose()
