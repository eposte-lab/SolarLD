"""OSM building lookup via Overpass API.

When Google Solar API ``buildingInsights:findClosest`` returns 404 for
coordinates that we *think* are accurate, the typical cause is a 30-80m
offset between the geocoded address and the actual rooftop centroid:

    * Mapbox returns the parcel centroid, but the building sits on the
      back of the parcel.
    * The Atoka address resolves to the road segment in front of the
      property rather than the building itself.
    * The original coordinate landed in the gap between two buildings
      in a dense industrial cluster.

Solar API has a ~100m search radius internally but only returns the
nearest building if it sits inside its index — which doesn't include
every Italian rural / industrial-zone roof.

This module solves that with a free fallback: query OpenStreetMap's
Overpass API for ``way[building]`` polygons within a configurable
radius, pick the nearest by centroid distance, and hand back the
snapped coordinates so the caller can retry Solar API at a point that
actually sits on a roof.

Cost: free (Overpass is open data; we follow fair-use rate limiting).
Latency: typical p95 ~ 800ms-2s on overpass-api.de.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import httpx

from ..core.logging import get_logger

log = get_logger(__name__)


# Public Overpass endpoints (round-robin for resilience). Both are CORS-
# friendly, accept POST application/x-www-form-urlencoded, and do not
# require an API key. They have soft fair-use limits (~10k queries/day,
# ~1 query/s sustained) which fit our demo-pipeline traffic comfortably.
_OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

# Default search radius — generous enough to catch the typical Atoka /
# Mapbox geocode-to-rooftop offset (~30-50m for SMEs, up to ~80m in
# industrial clusters), tight enough to avoid grabbing the wrong building
# when the geocode landed at a road junction.
DEFAULT_SEARCH_RADIUS_M = 80


@dataclass(slots=True)
class BuildingMatch:
    """Result of a successful nearest-building query.

    ``distance_m`` is the haversine distance from the query point to the
    matched building's centroid — exposed so callers can decide whether
    the snap is trustworthy (e.g. reject if > 60m to avoid landing on a
    neighbour's roof).
    """

    lat: float
    lng: float
    distance_m: float
    osm_id: int
    tags: dict[str, str]


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine distance in metres. Sufficiently accurate at < 1 km."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def _polygon_centroid(coords: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Centroid of a (possibly open) lat/lng polygon ring.

    Falls back to the arithmetic mean when the shoelace-area is degenerate
    (collinear vertices) — that happens for buildings represented as a
    ring of only 3-4 points where the polygon area calculation can
    underflow on equatorial latitudes.
    """
    if not coords:
        return None
    if len(coords) < 3:
        # Single node or 2-point way — just return the average.
        avg_lat = sum(c[0] for c in coords) / len(coords)
        avg_lng = sum(c[1] for c in coords) / len(coords)
        return avg_lat, avg_lng

    # Shoelace formula. Treat lat/lng as planar — fine for the small
    # extents (< 200m) we deal with here.
    area = 0.0
    cx = 0.0
    cy = 0.0
    n = len(coords)
    for i in range(n):
        x0, y0 = coords[i][1], coords[i][0]  # (lng, lat)
        x1, y1 = coords[(i + 1) % n][1], coords[(i + 1) % n][0]
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    area *= 0.5
    if abs(area) < 1e-12:
        # Degenerate — fall back to mean.
        avg_lat = sum(c[0] for c in coords) / n
        avg_lng = sum(c[1] for c in coords) / n
        return avg_lat, avg_lng
    cx /= 6 * area
    cy /= 6 * area
    return cy, cx  # back to (lat, lng)


def _build_query(lat: float, lng: float, radius_m: int) -> str:
    """Overpass QL query for any way / relation tagged ``building=*``.

    ``out tags geom`` requests both the tag bag and the full geometry of
    each way so we can compute the centroid client-side without a
    second round-trip. ``timeout:10`` caps server-side execution at
    10 seconds — we then enforce a 12s client timeout on top.
    """
    return (
        "[out:json][timeout:10];"
        f"(way(around:{radius_m},{lat:.7f},{lng:.7f})[building];"
        f" relation(around:{radius_m},{lat:.7f},{lng:.7f})[building];);"
        "out tags geom;"
    )


def _parse_overpass(payload: dict[str, Any], lat: float, lng: float) -> list[BuildingMatch]:
    """Project Overpass JSON into ``BuildingMatch`` objects sorted by distance."""
    elements = payload.get("elements") or []
    out: list[BuildingMatch] = []
    for elem in elements:
        geom = elem.get("geometry") or []
        if not geom:
            continue
        try:
            coords = [(g["lat"], g["lon"]) for g in geom]
        except (KeyError, TypeError):
            continue
        centroid = _polygon_centroid(coords)
        if not centroid:
            continue
        c_lat, c_lng = centroid
        d = _haversine_m(lat, lng, c_lat, c_lng)
        out.append(
            BuildingMatch(
                lat=c_lat,
                lng=c_lng,
                distance_m=d,
                osm_id=int(elem.get("id") or 0),
                tags=dict(elem.get("tags") or {}),
            )
        )
    out.sort(key=lambda m: m.distance_m)
    return out


async def find_nearest_building(
    lat: float,
    lng: float,
    *,
    max_distance_m: int = DEFAULT_SEARCH_RADIUS_M,
    client: httpx.AsyncClient | None = None,
    endpoints: tuple[str, ...] = _OVERPASS_ENDPOINTS,
    timeout_s: float = 12.0,
) -> BuildingMatch | None:
    """Return the nearest OSM building polygon centroid to (lat, lng).

    Returns ``None`` when:
        * Overpass returns no ``building=*`` element within the radius
        * every endpoint fails (network, 5xx, timeout) — we degrade
          gracefully and let the caller fall back to a static aerial.

    The function never raises — failure modes are logged and surface as
    ``None`` so the caller doesn't have to wrap each call in a try/except.
    """
    query = _build_query(lat, lng, max_distance_m)
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=timeout_s)

    try:
        for endpoint in endpoints:
            try:
                resp = await client.post(
                    endpoint,
                    data={"data": query},
                    headers={"User-Agent": "solarlead-osm-snap/1.0"},
                )
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                log.warning(
                    "osm_building.endpoint_error",
                    endpoint=endpoint,
                    err=type(exc).__name__,
                )
                continue

            if resp.status_code >= 500:
                log.warning(
                    "osm_building.endpoint_5xx",
                    endpoint=endpoint,
                    status=resp.status_code,
                )
                continue
            if resp.status_code >= 400:
                # 4xx is usually a malformed query — no point trying the
                # second endpoint, but log loudly so we can fix the QL.
                log.error(
                    "osm_building.endpoint_4xx",
                    endpoint=endpoint,
                    status=resp.status_code,
                    body=resp.text[:200],
                )
                return None

            try:
                payload = resp.json()
            except ValueError as exc:
                log.warning(
                    "osm_building.invalid_json",
                    endpoint=endpoint,
                    err=str(exc)[:120],
                )
                continue

            matches = _parse_overpass(payload, lat, lng)
            if not matches:
                log.info(
                    "osm_building.no_match",
                    lat=lat,
                    lng=lng,
                    radius_m=max_distance_m,
                )
                return None
            best = matches[0]
            log.info(
                "osm_building.match",
                lat=lat,
                lng=lng,
                snapped_lat=best.lat,
                snapped_lng=best.lng,
                distance_m=round(best.distance_m, 1),
                osm_id=best.osm_id,
            )
            return best

        # All endpoints exhausted.
        log.warning(
            "osm_building.all_endpoints_failed",
            lat=lat,
            lng=lng,
        )
        return None

    finally:
        if owns_client:
            await client.aclose()
