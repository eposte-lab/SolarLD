"""Mapbox client — used as fallback when Google Solar has no building at a point.

Two jobs:
  1) **Reverse geocoding** — turn a lat/lng into an Italian address
     (`comune`, `provincia`, `cap`) since the Google Solar payload is not
     always populated with locality fields.
  2) **Static Imagery** — fetch a high-zoom satellite tile we hand off to
     Claude Vision (Sprint 2) to estimate roof geometry when Google has no
     coverage.

The fallback roof-geometry detection itself runs in
`src/agents/hunter_fallback.py` which calls Claude; this module only
provides the primitive HTTP wrappers.
"""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

# Media types Anthropic accepts for a base64 image block.
_ALLOWED_IMAGE_MEDIA = {"image/jpeg", "image/png", "image/gif", "image/webp"}

GEOCODE_ENDPOINT = "https://api.mapbox.com/geocoding/v5/mapbox.places/{lng},{lat}.json"
# Forward-geocode uses the same REST family but with the address as the
# path segment. Mapbox URL-encodes automatically when we pass via `httpx`.
FORWARD_GEOCODE_ENDPOINT = "https://api.mapbox.com/geocoding/v5/mapbox.places/{query}.json"
STATIC_ENDPOINT = (
    "https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/"
    "{lng},{lat},{zoom},0/{width}x{height}@2x"
)


class MapboxError(Exception):
    """Raised when the Mapbox API returns an error response."""


@dataclass(slots=True)
class ReverseGeocodeResult:
    address: str | None
    cap: str | None
    comune: str | None
    provincia: str | None
    region: str | None
    country: str | None


def _extract_context_value(feature: dict, prefix: str) -> str | None:
    """Walk a Mapbox feature's `context` array looking for a given prefix."""
    for ctx in feature.get("context", []):
        cid = str(ctx.get("id", ""))
        if cid.startswith(prefix):
            short = ctx.get("short_code")
            if short and prefix == "region":
                # Short code like 'IT-NA' — keep only the province letters
                return short.split("-")[-1].upper()
            return ctx.get("text")
    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
async def reverse_geocode(
    lat: float,
    lng: float,
    *,
    client: httpx.AsyncClient | None = None,
    token: str | None = None,
) -> ReverseGeocodeResult:
    """Reverse-geocode a coordinate to an Italian postal address."""
    access_token = token or settings.mapbox_access_token
    if not access_token:
        raise MapboxError("MAPBOX_ACCESS_TOKEN not configured")

    url = GEOCODE_ENDPOINT.format(lat=lat, lng=lng)
    params = {
        "access_token": access_token,
        "types": "address,postcode,place,region",
        "country": "it",
        "language": "it",
        "limit": 1,
    }

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
    try:
        resp = await client.get(url, params=params)
    finally:
        if owns_client:
            await client.aclose()

    if resp.status_code >= 400:
        raise MapboxError(f"geocode status={resp.status_code} body={resp.text[:200]}")

    data = resp.json()
    features = data.get("features") or []
    if not features:
        return ReverseGeocodeResult(
            address=None, cap=None, comune=None, provincia=None, region=None, country="IT"
        )

    feat = features[0]
    return ReverseGeocodeResult(
        address=feat.get("place_name"),
        cap=_extract_context_value(feat, "postcode"),
        comune=_extract_context_value(feat, "place"),
        provincia=_extract_context_value(feat, "region"),
        region=_extract_context_value(feat, "region"),
        country="IT",
    )


@dataclass(slots=True)
class ForwardGeocodeResult:
    """One resolved candidate from a forward-geocode lookup.

    ``relevance`` is Mapbox's 0..1 match confidence — we expose it so the
    HunterAgent can reject ambiguous matches (e.g. a street that exists in
    both Rome and Milan).
    """

    lat: float
    lng: float
    address: str | None
    cap: str | None
    comune: str | None
    provincia: str | None
    relevance: float


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
async def forward_geocode(
    address: str,
    *,
    country: str = "it",
    proximity: tuple[float, float] | None = None,
    min_relevance: float = 0.75,
    client: httpx.AsyncClient | None = None,
    token: str | None = None,
) -> ForwardGeocodeResult | None:
    """Resolve a postal address to (lat, lng) + normalized components.

    Used by HunterAgent's ATECO-precision pipeline to turn an Atoka HQ
    address into a coordinate we can feed into Google Solar.

    Returns ``None`` when:
      * Mapbox returns zero features (address unknown)
      * The top feature's relevance is below ``min_relevance`` (ambiguous
        match — safer to skip than to geocode to the wrong city).

    Raises:
        MapboxError: on HTTP errors / missing token (tenacity will retry on
            transient 5xx before surfacing).
    """
    access_token = token or settings.mapbox_access_token
    if not access_token:
        raise MapboxError("MAPBOX_ACCESS_TOKEN not configured")
    if not address or not address.strip():
        return None

    # Mapbox's search box tolerates a trailing city/country; we pass the
    # address as-is and let their parser handle it. Strip any newlines the
    # Atoka payload might have embedded.
    query = address.replace("\n", ", ").strip()

    url = FORWARD_GEOCODE_ENDPOINT.format(query=query)
    params: dict[str, str | float | int] = {
        "access_token": access_token,
        "country": country,
        "language": "it",
        "limit": 1,
        # 'address,poi' biases toward street-level matches; we don't want
        # Mapbox returning a whole comune as a match for a precise HQ.
        "types": "address,poi",
    }
    if proximity is not None:
        params["proximity"] = f"{proximity[1]},{proximity[0]}"  # lng,lat

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
    try:
        resp = await client.get(url, params=params)
    finally:
        if owns_client:
            await client.aclose()

    if resp.status_code >= 400:
        raise MapboxError(f"forward_geocode status={resp.status_code} body={resp.text[:200]}")

    data = resp.json()
    features = data.get("features") or []
    if not features:
        return None

    feat = features[0]
    relevance = float(feat.get("relevance") or 0.0)
    if relevance < min_relevance:
        log.debug(
            "forward_geocode_low_relevance",
            query=query,
            relevance=relevance,
            threshold=min_relevance,
        )
        return None

    center = feat.get("center") or []
    if len(center) != 2:
        return None
    lng, lat = float(center[0]), float(center[1])

    return ForwardGeocodeResult(
        lat=lat,
        lng=lng,
        address=feat.get("place_name"),
        cap=_extract_context_value(feat, "postcode"),
        comune=_extract_context_value(feat, "place"),
        provincia=_extract_context_value(feat, "region"),
        relevance=relevance,
    )


def build_static_satellite_url(
    lat: float,
    lng: float,
    *,
    zoom: int = 19,
    width: int = 640,
    height: int = 640,
    token: str | None = None,
) -> str:
    """Build a Mapbox Static Images URL for a satellite tile at this point.

    Used by the Claude-Vision roof fallback: the URL is passed as an image
    block and Claude estimates the roof polygon.
    """
    access_token = token or settings.mapbox_access_token
    if not access_token:
        raise MapboxError("MAPBOX_ACCESS_TOKEN not configured")
    base = STATIC_ENDPOINT.format(lat=lat, lng=lng, zoom=zoom, width=width, height=height)
    return f"{base}?access_token={access_token}"


# Equatorial circumference of the Earth (WGS-84) in metres — used to
# derive the Web-Mercator ground resolution of a static tile.
_EARTH_CIRCUMFERENCE_M = 40_075_016.686


@dataclass(slots=True)
class StaticSatelliteTile:
    """A downloaded Mapbox satellite tile plus its lat/lng ↔ pixel georef.

    ``scale_x`` / ``scale_y`` are degrees per *image* pixel (the file is
    ``@2x``, so twice the requested logical size). The georef tuple has
    the same shape the solar rendering crop expects.
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
async def fetch_static_satellite(
    lat: float,
    lng: float,
    *,
    zoom: int = 19,
    size: int = 800,
    client: httpx.AsyncClient | None = None,
    token: str | None = None,
) -> StaticSatelliteTile:
    """Download a high-res Mapbox satellite tile centred on ``(lat, lng)``.

    Returns the raw image bytes together with a Web-Mercator georef
    (linearised over the small tile — sub-pixel error at building scale)
    so callers can map panel lat/lng onto the imagery.
    """
    url = build_static_satellite_url(lat, lng, zoom=zoom, width=size, height=size, token=token)

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=30.0)
    try:
        resp = await http.get(url)
        if resp.status_code >= 400:
            raise MapboxError(f"static satellite status={resp.status_code}")
        image_bytes = resp.content
    finally:
        if owns_client:
            await http.aclose()

    # The @2x file is twice the requested logical size.
    img_px = size * 2
    cos_lat = math.cos(math.radians(lat))
    # Ground metres per device pixel (Web Mercator, @2x halves it).
    m_per_px = cos_lat * _EARTH_CIRCUMFERENCE_M / (256 * (2**zoom)) / 2.0
    scale_y = m_per_px / 111_320.0
    scale_x = m_per_px / (111_320.0 * cos_lat) if cos_lat > 0 else scale_y
    west_lng = lng - scale_x * img_px / 2.0
    north_lat = lat + scale_y * img_px / 2.0
    return StaticSatelliteTile(
        image_bytes=image_bytes,
        west_lng=west_lng,
        north_lat=north_lat,
        scale_x=scale_x,
        scale_y=scale_y,
    )


async def fetch_image_base64_block(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Download an image and return an Anthropic **base64** image content block.

    Vision callers must NOT hand Claude a Mapbox URL directly: Anthropic's
    URL-image fetcher honours the target's ``robots.txt``, and Mapbox disallows
    it — the request fails with HTTP 400 "This URL is disallowed by the
    website's robots.txt file." We are an *authorised* client (the access token
    is in the URL), so we fetch the bytes ourselves and pass base64; Anthropic
    never fetches anything, so robots.txt never applies.

    Raises ``MapboxError`` on any HTTP/transport failure so callers can treat it
    like the other Mapbox primitives.
    """
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=30.0)
    try:
        resp = await http.get(url)
        if resp.status_code >= 400:
            raise MapboxError(f"image fetch status={resp.status_code}")
        raw = resp.content
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    except httpx.HTTPError as exc:  # network / timeout / DNS
        raise MapboxError(f"image fetch failed: {exc}") from exc
    finally:
        if owns_client:
            await http.aclose()

    media_type = ctype if ctype in _ALLOWED_IMAGE_MEDIA else "image/png"
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(raw).decode("ascii"),
        },
    }
