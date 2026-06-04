"""Google Maps Static API client — satellite imagery fallback used when
Google Solar dataLayers has no coverage at a point.

Keeps the whole imagery stack on a single Google account (no Mapbox
dependency for rendering). Returns the same ``(image_bytes, georef)``
shape the solar renderer expects, so it's a drop-in for the previous
Mapbox fallback.

Key resolution: prefers a dedicated ``GOOGLE_MAPS_STATIC_API_KEY``; falls
back to ``GOOGLE_SOLAR_API_KEY`` — so the operator can simply enable the
"Maps Static API" on the existing Solar key's project instead of managing
a second key.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

STATIC_ENDPOINT = "https://maps.googleapis.com/maps/api/staticmap"

# Equatorial circumference of the Earth (WGS-84) in metres — used to derive
# the Web-Mercator ground resolution of a static tile (same math as Mapbox).
_EARTH_CIRCUMFERENCE_M = 40_075_016.686


class GoogleStaticError(Exception):
    """Raised when the Maps Static API returns an error or no key is set."""


def maps_static_key() -> str:
    """Dedicated Maps Static key, else the Solar key (same Google project)."""
    return (settings.google_maps_static_api_key or settings.google_solar_api_key or "").strip()


@dataclass(slots=True)
class StaticSatelliteTile:
    """A downloaded satellite tile plus its lat/lng ↔ pixel georef.

    ``scale_x`` / ``scale_y`` are degrees per *image* pixel (``scale=2`` so
    twice the requested logical size). Same shape the solar crop expects.
    """

    image_bytes: bytes
    west_lng: float
    north_lat: float
    scale_x: float
    scale_y: float

    @property
    def georef(self) -> tuple[float, float, float, float]:
        return self.west_lng, self.north_lat, self.scale_x, self.scale_y


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def fetch_google_static_satellite(
    lat: float,
    lng: float,
    *,
    zoom: int = 19,
    size: int = 640,
    client: httpx.AsyncClient | None = None,
) -> StaticSatelliteTile:
    """Download a high-zoom Google satellite tile centred on ``(lat, lng)``.

    ``size`` is the logical edge in px (Google's free max is 640); ``scale=2``
    doubles it to 1280 device px. Returns the raw bytes + a linearised
    Web-Mercator georef so callers can map panel lat/lng onto the imagery.
    """
    key = maps_static_key()
    if not key:
        raise GoogleStaticError("no Maps Static / Solar API key configured")

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=30.0)
    last_error = "unknown"
    try:
        # Step-down over BOTH zoom and scale. In rural/agricultural areas
        # Google often lacks a high-resolution (scale=2) satellite tile and
        # returns 403 ("...satellite imagery is not available...") for EVERY
        # zoom — because scale=2 is the constant, not the zoom. So for each
        # zoom we also try scale=1 (standard-res satellite, far wider
        # coverage). The downstream crop reframes the building either way, so
        # we take the first (zoom, scale) combo that returns imagery, ordered
        # to prefer the sharpest available image.
        cos_lat = math.cos(math.radians(lat))
        for z in range(zoom, 15, -1):
            for scale in (2, 1):
                params: dict[str, str | int] = {
                    "center": f"{lat},{lng}",
                    "zoom": z,
                    "size": f"{size}x{size}",
                    "scale": scale,
                    "maptype": "satellite",
                    "format": "png",
                    "key": key,
                }
                resp = await http.get(STATIC_ENDPOINT, params=params)
                if resp.status_code == 200:
                    image_bytes = resp.content
                    # The file is ``scale`` times the requested logical size.
                    img_px = size * scale
                    # Ground metres per device pixel (Web Mercator).
                    m_per_px = (
                        cos_lat * _EARTH_CIRCUMFERENCE_M / (256 * (2**z)) / float(scale)
                    )
                    scale_y = m_per_px / 111_320.0
                    scale_x = (
                        m_per_px / (111_320.0 * cos_lat) if cos_lat > 0 else scale_y
                    )
                    if z != zoom or scale != 2:
                        log.info(
                            "google_static.stepdown",
                            lat=lat,
                            lng=lng,
                            used_zoom=z,
                            used_scale=scale,
                        )
                    return StaticSatelliteTile(
                        image_bytes=image_bytes,
                        west_lng=lng - scale_x * img_px / 2.0,
                        north_lat=lat + scale_y * img_px / 2.0,
                        scale_x=scale_x,
                        scale_y=scale_y,
                    )
                # Keep the FULL-ish body of the last error for diagnosis —
                # Google's 403 text tells us whether it's coverage, key
                # restriction, or quota.
                last_error = (
                    f"z={z} scale={scale} status={resp.status_code} "
                    f"body={resp.text[:240]!r}"
                )
        raise GoogleStaticError(
            f"maps static failed at all zooms {zoom}..16 (scale 2+1): {last_error}"
        )
    finally:
        if owns_client:
            await http.aclose()
