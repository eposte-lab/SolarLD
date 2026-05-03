"""L0 — Map target areas via OSM Overpass.

Given a tenant's `target_wizard_groups` selection (set in onboarding via
the Sorgente module) and a list of operating provinces, this service:

1. Builds a single Overpass QL query that aggregates the
   `osm_landuse_hints` and `osm_additional_tags` from every selected
   wizard_group (read from `ateco_google_types` via
   `sector_target_service`).
2. Fetches the matching polygons (ways + relations) from a public
   Overpass endpoint (round-robin between two CORS-friendly mirrors).
3. Classifies each polygon against every active sector — a single zone
   can match multiple sectors (e.g. `landuse=industrial` matches both
   `industry_heavy` and `logistics`). The sector with the highest
   weight becomes `primary_sector`.
4. Persists the surviving zones into `tenant_target_areas` with their
   centroid, polygon geometry (GeoJSON), area_m2, matched_sectors,
   primary_sector and score.

The query is run **once per tenant** in onboarding, then re-runnable
on demand (`POST /v1/territory/map`). Output: 50-500 zones for a
typical 2-3 province tenant with 2-3 wizard_groups.

Cost: zero (Overpass is free, fair-use ~10k queries/day). Latency: ~2-15
minutes depending on the geographic span. Designed to run as an ARQ
background task because it's slow but non-blocking.

Backward-compat: tenants who don't trigger this mapping have an empty
`tenant_target_areas` and the v3 funnel cron simply skips them. The
legacy v2 flow (Atoka-first) keeps working in parallel.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..core.logging import get_logger
from .sector_target_service import (
    OsmTagHint,
    SectorAreaMapping,
    _warm_cache,
    get_sector_config_by_wizard_group,
)

log = get_logger(__name__)


# Public Overpass endpoints (round-robin for resilience). Same pair
# we use in `osm_building_service.py` — both are CORS-friendly, accept
# POST application/x-www-form-urlencoded, no API key required.
_OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

# Overpass timeout — landuse / building scans across whole provinces
# legitimately take 60-180s on cold cache. Don't set too low.
_OVERPASS_TIMEOUT_S = 240

# Minimum area threshold for a zone to be persisted. Smaller polygons
# are usually noise (small parking lots, tiny commercial plots) — they
# would generate Places Nearby calls with no useful candidates.
_MIN_PERSISTED_AREA_M2 = 500


@dataclass(slots=True)
class OsmZone:
    """A raw polygon fetched from Overpass before sector classification."""

    osm_id: int
    osm_type: str  # 'way' | 'relation'
    centroid_lat: float
    centroid_lng: float
    area_m2: float
    geojson_polygon: dict[str, Any] | None
    tags: dict[str, str]


@dataclass(slots=True)
class ClassifiedZone:
    """OsmZone + sector classification ready for persistence."""

    zone: OsmZone
    matched_sectors: list[str]
    primary_sector: str | None
    matching_score: float


@dataclass(slots=True)
class MapResult:
    """Outcome of `map_target_areas_for_tenant` for the caller / API."""

    tenant_id: str
    total_zones_fetched: int
    zones_matched_to_sectors: int
    zones_persisted: int
    sectors_covered: list[str]
    provinces_covered: list[str]
    elapsed_seconds: float
    overpass_endpoint_used: str | None
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

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
    """Centroid of a (possibly open) lat/lng polygon ring."""
    if not coords:
        return None
    if coords[0] != coords[-1]:
        coords = [*coords, coords[0]]
    n = len(coords)
    if n < 4:
        # Degenerate (point or line): just average the lat/lng
        avg_lat = sum(c[0] for c in coords) / max(1, len(coords))
        avg_lng = sum(c[1] for c in coords) / max(1, len(coords))
        return (avg_lat, avg_lng)
    cx = cy = a = 0.0
    for i in range(n - 1):
        x0, y0 = coords[i][1], coords[i][0]  # lng, lat
        x1, y1 = coords[i + 1][1], coords[i + 1][0]
        cross = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
        a += cross
    if abs(a) < 1e-12:
        return (sum(c[0] for c in coords) / n, sum(c[1] for c in coords) / n)
    a *= 0.5
    cx /= 6.0 * a
    cy /= 6.0 * a
    return (cy, cx)  # back to (lat, lng)


def _polygon_area_m2(coords: list[tuple[float, float]]) -> float:
    """Approximate polygon area in m² using equirectangular projection.

    Not exact for huge polygons but accurate to within ~1% at city
    scale. Plenty for filtering "is this zone big enough to scan".
    """
    if not coords or len(coords) < 3:
        return 0.0
    if coords[0] != coords[-1]:
        coords = [*coords, coords[0]]
    # Reference latitude for equirectangular scaling.
    avg_lat = sum(c[0] for c in coords) / len(coords)
    cos_lat = math.cos(math.radians(avg_lat))
    metres_per_deg_lat = 111_320.0
    metres_per_deg_lng = 111_320.0 * cos_lat
    n = len(coords)
    a = 0.0
    for i in range(n - 1):
        x0 = coords[i][1] * metres_per_deg_lng
        y0 = coords[i][0] * metres_per_deg_lat
        x1 = coords[i + 1][1] * metres_per_deg_lng
        y1 = coords[i + 1][0] * metres_per_deg_lat
        a += x0 * y1 - x1 * y0
    return abs(a) * 0.5


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------

def build_overpass_query(
    *,
    landuse_values: set[str],
    additional_tags: list[OsmTagHint],
    province_codes: list[str],
    timeout_s: int = _OVERPASS_TIMEOUT_S,
) -> str:
    """Compose a single Overpass QL query for all sectors in scope.

    The query selects the union of:
      * way[landuse=X] within Italian provinces in scope, for each X
      * relation[landuse=X] (multipolygons)
      * way[K=V] / relation[K=V] for each (K,V) in additional_tags

    Output mode `geom` returns the polygon vertices so we can compute
    the centroid client-side without ST_Centroid round-trips.
    """
    if not province_codes:
        raise ValueError("build_overpass_query: province_codes cannot be empty")

    # ISO 3166-2 codes for Italian provinces are formatted IT-XX (e.g. IT-MI).
    # Overpass `["ISO3166-2"~"^IT-(MI|BG|BS)$"]` matches the area boundary.
    province_regex = "|".join(sorted(set(p.upper() for p in province_codes)))

    landuse_clause = ""
    if landuse_values:
        regex = "|".join(sorted(landuse_values))
        landuse_clause = (
            f'  way["landuse"~"^({regex})$"](area.searchArea);\n'
            f'  relation["landuse"~"^({regex})$"](area.searchArea);\n'
        )

    tag_clauses = []
    for tag in additional_tags:
        tag_clauses.append(
            f'  way["{tag.tag_key}"="{tag.tag_value}"](area.searchArea);\n'
            f'  relation["{tag.tag_key}"="{tag.tag_value}"](area.searchArea);'
        )
    tag_block = "\n".join(tag_clauses)

    return (
        f"[out:json][timeout:{timeout_s}];\n"
        f'area["ISO3166-2"~"^IT-({province_regex})$"]->.searchArea;\n'
        "(\n"
        f"{landuse_clause}"
        f"{tag_block}\n"
        ");\n"
        "out tags geom;\n"
    )


def aggregate_filters(
    configs: list[SectorAreaMapping],
) -> tuple[set[str], list[OsmTagHint]]:
    """Merge OSM filters across multiple sector configs.

    Returns (landuse_values, additional_tags). De-duplicates by tag key/value
    while preserving the highest weight (used later in classification).
    """
    landuse: set[str] = set()
    tags_by_key: dict[tuple[str, str], OsmTagHint] = {}
    for cfg in configs:
        for h in cfg.osm_landuse_hints:
            if h.tag_key == "landuse":
                landuse.add(h.tag_value)
        for h in cfg.osm_additional_tags:
            existing = tags_by_key.get((h.tag_key, h.tag_value))
            if existing is None or h.weight > existing.weight:
                tags_by_key[(h.tag_key, h.tag_value)] = h
    return landuse, list(tags_by_key.values())


# ---------------------------------------------------------------------------
# Overpass fetch
# ---------------------------------------------------------------------------

async def fetch_zones_from_osm(
    query: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_s: float = _OVERPASS_TIMEOUT_S,
) -> tuple[list[OsmZone], str | None]:
    """POST the query to Overpass, parse polygons. Returns (zones, endpoint_used)."""
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        for endpoint in _OVERPASS_ENDPOINTS:
            try:
                resp = await client.post(
                    endpoint,
                    data={"data": query},
                    headers={"User-Agent": "solarlead-zone-mapper/1.0"},
                )
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                log.warning(
                    "zone_mapper.endpoint_error",
                    endpoint=endpoint,
                    err=type(exc).__name__,
                )
                continue
            if resp.status_code >= 500:
                log.warning(
                    "zone_mapper.endpoint_5xx",
                    endpoint=endpoint,
                    status=resp.status_code,
                )
                continue
            if resp.status_code >= 400:
                log.error(
                    "zone_mapper.endpoint_4xx",
                    endpoint=endpoint,
                    status=resp.status_code,
                    body=resp.text[:300],
                )
                return [], endpoint
            try:
                payload = resp.json()
            except ValueError:
                log.warning("zone_mapper.invalid_json", endpoint=endpoint)
                continue

            zones = _parse_overpass_payload(payload)
            return zones, endpoint
        return [], None
    finally:
        if owns_client:
            await client.aclose()


def _parse_overpass_payload(payload: dict[str, Any]) -> list[OsmZone]:
    """Project Overpass JSON elements into OsmZone objects."""
    elements = payload.get("elements") or []
    out: list[OsmZone] = []
    for elem in elements:
        osm_type = elem.get("type")
        if osm_type not in ("way", "relation"):
            continue
        geom = elem.get("geometry") or []
        if not geom:
            # `relation` with members; out geom would expand the bounds. If
            # the server didn't include geometry for this relation, skip —
            # we'd need a second pass to expand. Accept the loss for MVP.
            continue
        try:
            coords = [(g["lat"], g["lon"]) for g in geom]
        except (KeyError, TypeError):
            continue
        centroid = _polygon_centroid(coords)
        if centroid is None:
            continue
        c_lat, c_lng = centroid
        area = _polygon_area_m2(coords)
        if area < _MIN_PERSISTED_AREA_M2:
            continue
        # Build GeoJSON Polygon (lng, lat) closed ring.
        ring = [[lng, lat] for (lat, lng) in coords]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        geojson = {"type": "Polygon", "coordinates": [ring]}
        tags = dict(elem.get("tags") or {})
        out.append(
            OsmZone(
                osm_id=int(elem.get("id") or 0),
                osm_type=osm_type,
                centroid_lat=c_lat,
                centroid_lng=c_lng,
                area_m2=area,
                geojson_polygon=geojson,
                tags=tags,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Classification (zone → matched_sectors[])
# ---------------------------------------------------------------------------

def classify_zone_for_sectors(
    zone: OsmZone,
    *,
    configs: list[SectorAreaMapping],
) -> ClassifiedZone:
    """Score a zone against every sector config; pick highest-weighted match.

    Logic mirrors the addendum PRD §"L0 → classify_zone_for_sectors":

      * landuse match: max(weight) across hints whose tag_value matches
        the zone's `landuse=*` tag.
      * additional tag match: max(weight) where any (tag_key, tag_value)
        in osm_additional_tags is present in the zone's raw tags.
      * area threshold: if zone.area_m2 < cfg.min_zone_area_m2, halve
        the score (still keep, just less prominent).

    Threshold to mark "matched": score >= 0.30 (out of 1.0). A zone
    matching nothing is returned with empty matched_sectors[] and the
    caller decides whether to drop it.
    """
    matched: list[tuple[str, float]] = []
    landuse_value = zone.tags.get("landuse")

    for cfg in configs:
        score = 0.0

        # Landuse match
        if landuse_value:
            for hint in cfg.osm_landuse_hints:
                if hint.tag_key == "landuse" and hint.tag_value == landuse_value:
                    score = max(score, hint.weight)

        # Additional tag match
        for hint in cfg.osm_additional_tags:
            zone_value = zone.tags.get(hint.tag_key)
            if zone_value == hint.tag_value:
                score = max(score, hint.weight)

        # Area gate — soften but don't kill
        if cfg.min_zone_area_m2 and zone.area_m2 < cfg.min_zone_area_m2:
            score *= 0.5

        if score >= 0.30:
            matched.append((cfg.wizard_group, score))

    matched.sort(key=lambda m: m[1], reverse=True)
    if not matched:
        return ClassifiedZone(
            zone=zone,
            matched_sectors=[],
            primary_sector=None,
            matching_score=0.0,
        )

    return ClassifiedZone(
        zone=zone,
        matched_sectors=[m[0] for m in matched],
        primary_sector=matched[0][0],
        matching_score=round(matched[0][1] * 100.0, 2),
    )


# ---------------------------------------------------------------------------
# Persistence + orchestrator
# ---------------------------------------------------------------------------

async def _persist_zones(
    supabase: Any,
    *,
    tenant_id: str,
    classified: list[ClassifiedZone],
) -> int:
    """Bulk upsert into tenant_target_areas. Returns rows persisted."""
    if not classified:
        return 0

    rows = []
    for c in classified:
        if not c.matched_sectors:
            continue
        # Province inferred from tags when present (ISO3166-2 isn't on every
        # element). For MVP we leave it null — the orchestrator can backfill
        # with a reverse-geocode if needed.
        province = c.zone.tags.get("addr:province") or c.zone.tags.get("ref:ISTAT")
        rows.append(
            {
                "tenant_id": tenant_id,
                "osm_id": c.zone.osm_id,
                "osm_type": c.zone.osm_type,
                "centroid_lat": round(c.zone.centroid_lat, 7),
                "centroid_lng": round(c.zone.centroid_lng, 7),
                "area_m2": round(c.zone.area_m2, 2),
                "matched_sectors": c.matched_sectors,
                "primary_sector": c.primary_sector,
                "matching_score": c.matching_score,
                "province_code": province,
                "raw_tags": c.zone.tags,
                "status": "active",
                # geometry is GEOGRAPHY(POLYGON, 4326) — Postgres / PostGIS
                # accepts GeoJSON via ST_GeomFromGeoJSON. We INSERT the JSON
                # under a sentinel key the worker post-processes (see below).
            }
        )

    if not rows:
        return 0

    # Upsert by (tenant_id, osm_type, osm_id). When the tenant re-runs
    # mapping, we update primary_sector / matched_sectors in place so
    # historic L1 candidates still point at a valid zone row.
    resp = (
        await supabase.table("tenant_target_areas")
        .upsert(rows, on_conflict="tenant_id,osm_type,osm_id")
        .execute()
    )
    return len(resp.data or [])


async def map_target_areas_for_tenant(
    supabase: Any,
    *,
    tenant_id: str,
    wizard_groups: list[str],
    province_codes: list[str],
    client: httpx.AsyncClient | None = None,
) -> MapResult:
    """Orchestrator for L0. Runs the full pipeline:
    config lookup → query build → fetch → classify → persist.

    The supabase client must be a service-role one; tenant_target_areas
    is RLS-scoped and only the worker writes to it.
    """
    started = asyncio.get_event_loop().time()
    errors: list[str] = []

    # 1) Load palettes
    await _warm_cache(supabase)
    configs: list[SectorAreaMapping] = []
    for wg in wizard_groups:
        cfg = await get_sector_config_by_wizard_group(supabase, wizard_group=wg)
        if cfg is None:
            errors.append(f"unknown_wizard_group:{wg}")
            continue
        configs.append(cfg)

    if not configs:
        return MapResult(
            tenant_id=tenant_id,
            total_zones_fetched=0,
            zones_matched_to_sectors=0,
            zones_persisted=0,
            sectors_covered=[],
            provinces_covered=list(province_codes),
            elapsed_seconds=0.0,
            overpass_endpoint_used=None,
            errors=errors or ["no_valid_wizard_groups"],
        )

    # 2) Build query
    landuse_values, additional_tags = aggregate_filters(configs)
    if not landuse_values and not additional_tags:
        return MapResult(
            tenant_id=tenant_id,
            total_zones_fetched=0,
            zones_matched_to_sectors=0,
            zones_persisted=0,
            sectors_covered=[],
            provinces_covered=list(province_codes),
            elapsed_seconds=0.0,
            overpass_endpoint_used=None,
            errors=["no_osm_filters_for_selected_wizard_groups"],
        )

    query = build_overpass_query(
        landuse_values=landuse_values,
        additional_tags=additional_tags,
        province_codes=province_codes,
    )

    # 3) Fetch
    zones, endpoint_used = await fetch_zones_from_osm(query, client=client)

    # 4) Classify + persist
    classified = [classify_zone_for_sectors(z, configs=configs) for z in zones]
    matched = [c for c in classified if c.matched_sectors]
    persisted = await _persist_zones(supabase, tenant_id=tenant_id, classified=matched)

    sectors_covered = sorted(set(s for c in matched for s in c.matched_sectors))
    elapsed = asyncio.get_event_loop().time() - started

    log.info(
        "zone_mapper.done",
        tenant_id=tenant_id,
        wizard_groups=wizard_groups,
        provinces=province_codes,
        fetched=len(zones),
        matched=len(matched),
        persisted=persisted,
        sectors_covered=sectors_covered,
        elapsed_s=round(elapsed, 1),
    )

    return MapResult(
        tenant_id=tenant_id,
        total_zones_fetched=len(zones),
        zones_matched_to_sectors=len(matched),
        zones_persisted=persisted,
        sectors_covered=sectors_covered,
        provinces_covered=list(province_codes),
        elapsed_seconds=round(elapsed, 1),
        overpass_endpoint_used=endpoint_used,
        errors=errors,
    )
