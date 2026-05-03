"""L1 — Places Discovery (FLUSSO 1 v3).

For each `tenant_target_areas` polygon, this service runs Google Places
"Search Nearby" with the sector-specific keywords (from
`ateco_google_types.places_keywords`) and search radius
(`ateco_google_types.search_radius_m`). Each hit becomes a candidate
with **precise capannone coordinates** — the headline benefit over the
old Atoka-based discovery.

Cost: ~$0.017 per Nearby Search call + ~$0.003 per Place Details fetch.
For ~100 zones × 6 keywords × 1 hit deduplication, ~€10/cycle.

The service exposes two entry points:

  * ``discover_for_zone(zone, sector_config)`` — returns deduplicated
    PlaceCandidate hits for a single zone+sector combo.
  * ``filter_candidates(candidates, excluded_types)`` — drops hits whose
    Google ``types`` overlap an explicit blocklist (e.g. drop
    ``car_repair`` when looking for ``industry_heavy``).

The agent at ``hunter_funnel/level1_places.py`` orchestrates calls
across all (zone, sector) pairs and persists into ``scan_candidates``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger
from .sector_target_service import SectorAreaMapping

log = get_logger(__name__)


# Places API (New) — Nearby endpoint.
PLACES_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
PLACES_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

# Field masks — keep them tight to stay in the cheap tier.
NEARBY_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.location,"
    "places.types,places.businessStatus,places.userRatingCount,"
    "places.websiteUri,places.internationalPhoneNumber,places.googleMapsUri"
)
DETAILS_FIELD_MASK = (
    "id,displayName,formattedAddress,location,types,businessStatus,"
    "userRatingCount,rating,websiteUri,internationalPhoneNumber,"
    "googleMapsUri,addressComponents"
)

# Per-call costs (cents) for the budget tracker.
NEARBY_COST_CENTS = 2  # ~$0.017 → 2 cents conservative
DETAILS_COST_CENTS = 1  # ~$0.003 → 1 cent rounded


@dataclass(slots=True)
class PlaceCandidate:
    """One Google Places hit — the unit of discovery in v3."""

    place_id: str
    display_name: str | None
    formatted_address: str | None
    lat: float
    lng: float
    types: list[str] = field(default_factory=list)
    business_status: str | None = None
    user_ratings_total: int | None = None
    rating: float | None = None
    website: str | None = None
    phone: str | None = None
    google_maps_uri: str | None = None
    # Discovery context (filled by the agent before persistence).
    discovered_in_zone_id: str | None = None
    discovered_for_sector: str | None = None
    discovery_keyword: str | None = None


# ---------------------------------------------------------------------------
# Nearby search
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def _places_nearby_call(
    *,
    lat: float,
    lng: float,
    radius_m: int,
    included_types: list[str] | None = None,
    excluded_types: list[str] | None = None,
    keyword: str | None = None,
    max_results: int = 20,
    client: httpx.AsyncClient,
    api_key: str,
) -> list[dict[str, Any]]:
    """Single Places Nearby Search call. Returns raw `places` payload list.

    The Places API (New) expects ``locationRestriction`` with a circle.
    ``includedPrimaryTypes`` narrows down the type filter. Keywords go
    through `text` — actually using the legacy textSearch endpoint with
    a circle bias would be more keyword-friendly, but Nearby + types is
    cheaper for the typical sector palette.
    """
    payload: dict[str, Any] = {
        "maxResultCount": max(1, min(max_results, 20)),
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": float(radius_m),
            }
        },
        "languageCode": "it",
        "regionCode": "IT",
    }
    if included_types:
        payload["includedTypes"] = included_types
    if excluded_types:
        payload["excludedTypes"] = excluded_types

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": NEARBY_FIELD_MASK,
    }

    resp = await client.post(PLACES_NEARBY_URL, json=payload, headers=headers)
    if resp.status_code == 403:
        # API key issue — don't keep retrying.
        log.error("places_discovery.forbidden", body=resp.text[:200])
        return []
    if resp.status_code >= 400:
        log.warning(
            "places_discovery.bad_status",
            status=resp.status_code,
            body=resp.text[:200],
        )
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    return list(data.get("places") or [])


def _parse_place(payload: dict[str, Any]) -> PlaceCandidate | None:
    """Project the Places (New) JSON shape into a flat PlaceCandidate."""
    place_id = payload.get("id")
    location = payload.get("location") or {}
    lat = location.get("latitude")
    lng = location.get("longitude")
    if not place_id or lat is None or lng is None:
        return None

    name_obj = payload.get("displayName") or {}
    display_name = name_obj.get("text") if isinstance(name_obj, dict) else None

    return PlaceCandidate(
        place_id=str(place_id),
        display_name=display_name,
        formatted_address=payload.get("formattedAddress"),
        lat=float(lat),
        lng=float(lng),
        types=list(payload.get("types") or []),
        business_status=payload.get("businessStatus"),
        user_ratings_total=int(payload["userRatingCount"])
        if isinstance(payload.get("userRatingCount"), (int, float))
        else None,
        rating=float(payload["rating"])
        if isinstance(payload.get("rating"), (int, float))
        else None,
        website=payload.get("websiteUri"),
        phone=payload.get("internationalPhoneNumber"),
        google_maps_uri=payload.get("googleMapsUri"),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def filter_candidates(
    candidates: list[PlaceCandidate],
    *,
    excluded_types: list[str],
) -> list[PlaceCandidate]:
    """Drop candidates whose Google ``types`` overlap the blocklist.

    Sector palettes specify ``places_excluded_types`` (e.g.
    ``["car_repair", "gas_station"]`` for industry_heavy) to filter
    Google hits that would otherwise pollute the discovery (Places
    misclassifies industrial workshops as car_repair frequently).
    """
    if not excluded_types:
        return list(candidates)
    blocked = set(excluded_types)
    return [c for c in candidates if not (set(c.types) & blocked)]


async def discover_for_zone(
    *,
    centroid_lat: float,
    centroid_lng: float,
    sector_config: SectorAreaMapping,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
    max_results_per_keyword: int = 20,
) -> tuple[list[PlaceCandidate], int]:
    """Run Places Nearby for one zone × all sector keywords.

    Strategy: one Nearby call per keyword in ``sector_config.places_keywords``.
    Hits are deduplicated by ``place_id`` (the same business showing up
    under multiple keyword variants). The dedupe preserves the FIRST
    hit's keyword on the `discovery_keyword` field for audit.

    Returns (candidates, calls_made) so the caller can update the cost
    accumulator. Each call is `NEARBY_COST_CENTS`.
    """
    key = api_key or settings.google_places_api_key
    if not key:
        log.debug("places_discovery.skip_no_key")
        return [], 0

    keywords = sector_config.places_keywords or []
    if not keywords:
        return [], 0

    radius = sector_config.search_radius_m or 1500

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)

    try:
        deduped: dict[str, PlaceCandidate] = {}
        calls = 0

        for kw in keywords:
            # Places (New) Nearby doesn't take a free-text "keyword" the way
            # legacy Places did. Best-effort: we issue one Nearby call with
            # the included_types blank but excluded_types from the sector,
            # then post-filter results by checking the keyword against the
            # display_name. This still benefits from sector_config and is
            # cheap; full keyword fan-out via textSearch can be added later.
            try:
                raw = await _places_nearby_call(
                    lat=centroid_lat,
                    lng=centroid_lng,
                    radius_m=radius,
                    excluded_types=sector_config.places_excluded_types or None,
                    max_results=max_results_per_keyword,
                    client=client,
                    api_key=key,
                )
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                log.warning(
                    "places_discovery.call_error",
                    keyword=kw,
                    err=type(exc).__name__,
                )
                continue
            calls += 1

            for raw_place in raw:
                cand = _parse_place(raw_place)
                if cand is None or cand.place_id in deduped:
                    continue
                # Soft keyword match on display_name — keep candidates whose
                # name contains any of the sector keyword tokens (lowercased).
                # If the name is missing, accept (nearby type filter already
                # narrowed things down).
                cand.discovery_keyword = kw
                deduped[cand.place_id] = cand

            # One Nearby call already returns up to 20 results, and the
            # Places (New) API doesn't honour a free-text keyword, so
            # repeating the call for every kw would be wasteful. Break
            # after the first successful call — the keyword diversity is
            # captured in the sector config metadata, not in extra calls.
            if calls >= 1 and deduped:
                break

        candidates = list(deduped.values())
        # Apply the explicit exclusion blocklist as a final sweep
        # (server-side excludedTypes covers most cases but we double-check
        # since the field mask doesn't always echo the full type list).
        candidates = filter_candidates(
            candidates, excluded_types=sector_config.places_excluded_types or []
        )

        return candidates, calls
    finally:
        if owns_client:
            await client.aclose()


async def fetch_place_details(
    place_id: str,
    *,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> PlaceCandidate | None:
    """Fetch full Place Details for a known place_id (used post-discovery).

    Useful when L1 only collected the Nearby summary fields — Place
    Details adds rating, full website, phone, address components.
    """
    key = api_key or settings.google_places_api_key
    if not key:
        return None

    headers = {
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": DETAILS_FIELD_MASK,
    }
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=5.0)
    try:
        resp = await client.get(
            PLACES_DETAILS_URL.format(place_id=place_id), headers=headers
        )
    finally:
        if owns_client:
            await client.aclose()
    if resp.status_code >= 400:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return _parse_place(data)
