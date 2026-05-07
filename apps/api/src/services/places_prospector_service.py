"""Trova aziende v3 — Google Places-based discovery service.

Replaces the Atoka-based prospector flow. The operator's "Trova aziende"
search now hits Google Places (New) API directly:

  1. Resolve geo anchor: comune (geocoded) → lat/lng, OR province
     centroid lookup, OR fallback to Places Text Search of the comune
     name.
  2. Run a single Places Nearby Search call with `includedPrimaryTypes`
     derived from the wizard_group sector (via `places_to_sector.py`),
     a circle locationRestriction, and an optional `textQuery` keyword.
  3. Return a list of `ProspectorPlace` dataclasses. The route persists
     them into `prospect_list_items` with `validation_status='pending'`,
     ready for the on-demand convalida flow.

Costs: ~$0.017 per Nearby call (one per search). The price is hidden
from the operator at the UI level — backed by the same key as the
funnel L1 discovery.

The on-demand convalida and outreach launches live in
`prospect_list_validation.py` and `prospect_list_outreach.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger
from ..data.province_centroids import province_centroid
from .places_to_sector import included_types_for_sector

log = get_logger(__name__)


# Places API (New) endpoints
PLACES_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Same field mask as the L1 funnel discovery for consistency.
NEARBY_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.location,"
    "places.types,places.businessStatus,places.userRatingCount,places.rating,"
    "places.websiteUri,places.internationalPhoneNumber,places.googleMapsUri"
)

# For the geocode helper — minimal mask, just lat/lng.
GEOCODE_FIELD_MASK = "places.location,places.formattedAddress"


@dataclass(slots=True)
class ProspectorPlace:
    """One Google Places hit returned to the dashboard /scoperta UI."""

    google_place_id: str
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


# ---------------------------------------------------------------------------
# Geo anchor resolution
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
)
async def _geocode_text(
    *, query: str, client: httpx.AsyncClient, api_key: str
) -> tuple[float, float] | None:
    """Resolve a free-text location to lat/lng via Places Text Search.

    We bias to Italy (regionCode='IT', languageCode='it') and ask only
    for the location field. Returns None on miss or non-2xx response.
    """
    payload: dict[str, Any] = {
        "textQuery": query,
        "languageCode": "it",
        "regionCode": "IT",
        "maxResultCount": 1,
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": GEOCODE_FIELD_MASK,
    }
    resp = await client.post(PLACES_TEXT_SEARCH_URL, json=payload, headers=headers)
    if resp.status_code >= 400:
        log.warning(
            "places_prospector.geocode_bad_status",
            status=resp.status_code,
            body=resp.text[:200],
        )
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    places = data.get("places") or []
    if not places:
        return None
    loc = places[0].get("location") or {}
    lat = loc.get("latitude")
    lng = loc.get("longitude")
    if lat is None or lng is None:
        return None
    return float(lat), float(lng)


async def resolve_anchor(
    *,
    comune: str | None,
    province_code: str | None,
    client: httpx.AsyncClient,
    api_key: str,
) -> tuple[float, float] | None:
    """Resolve the search geo anchor.

    Priority:
      1. comune passed → geocode "<comune>, <province>, Italia" via
         Places Text Search.
      2. province_code only → static centroid lookup (110 IT provinces).
      3. province_code with no static entry → geocode
         "Provincia di <code>, Italia" as fallback.
    """
    if comune:
        bias = comune.strip()
        bias = f"{bias}, {province_code.upper()}, Italia" if province_code else f"{bias}, Italia"
        latlng = await _geocode_text(query=bias, client=client, api_key=api_key)
        if latlng is not None:
            return latlng
        # Fall through to province lookup if geocode missed.

    if province_code:
        static_hit = province_centroid(province_code)
        if static_hit:
            return static_hit
        # Fallback: ask Places to geocode the province name.
        latlng = await _geocode_text(
            query=f"Provincia di {province_code.upper()}, Italia",
            client=client,
            api_key=api_key,
        )
        if latlng is not None:
            return latlng

    return None


# ---------------------------------------------------------------------------
# Nearby Search
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def _places_nearby(
    *,
    lat: float,
    lng: float,
    radius_m: int,
    included_types: list[str] | None,
    keyword: str | None,
    max_results: int,
    client: httpx.AsyncClient,
    api_key: str,
) -> list[dict[str, Any]]:
    """Single Nearby Search call. Returns the raw `places` list."""
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
        payload["includedPrimaryTypes"] = included_types
    # Note: the (New) Nearby endpoint ignores keyword — for keyword-aware
    # discovery the caller can split into a Text Search query (handled
    # one layer up).

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": NEARBY_FIELD_MASK,
    }
    resp = await client.post(PLACES_NEARBY_URL, json=payload, headers=headers)
    if resp.status_code == 403:
        log.error("places_prospector.forbidden", body=resp.text[:200])
        return []
    if resp.status_code >= 400:
        log.warning(
            "places_prospector.bad_status",
            status=resp.status_code,
            body=resp.text[:200],
        )
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    return list(data.get("places") or [])


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def _places_text_search(
    *,
    text_query: str,
    lat: float | None,
    lng: float | None,
    radius_m: int | None,
    included_type: str | None,
    max_results: int,
    client: httpx.AsyncClient,
    api_key: str,
) -> list[dict[str, Any]]:
    """Text Search variant — used when the operator passes a keyword.

    Text Search supports `textQuery` natively (Nearby does not). We bias
    via `locationBias.circle` (NOT `locationRestriction` — Text Search
    interprets restriction differently and may zero out results).
    """
    payload: dict[str, Any] = {
        "textQuery": text_query,
        "maxResultCount": max(1, min(max_results, 20)),
        "languageCode": "it",
        "regionCode": "IT",
    }
    if lat is not None and lng is not None and radius_m is not None:
        payload["locationBias"] = {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": float(radius_m),
            }
        }
    if included_type:
        # Text Search accepts a single primary type filter.
        payload["includedType"] = included_type

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": NEARBY_FIELD_MASK,
    }
    resp = await client.post(PLACES_TEXT_SEARCH_URL, json=payload, headers=headers)
    if resp.status_code >= 400:
        log.warning(
            "places_prospector.text_bad_status",
            status=resp.status_code,
            body=resp.text[:200],
        )
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    return list(data.get("places") or [])


def _parse_place(payload: dict[str, Any]) -> ProspectorPlace | None:
    place_id = payload.get("id")
    location = payload.get("location") or {}
    lat = location.get("latitude")
    lng = location.get("longitude")
    if not place_id or lat is None or lng is None:
        return None
    name_obj = payload.get("displayName") or {}
    display_name = name_obj.get("text") if isinstance(name_obj, dict) else None
    return ProspectorPlace(
        google_place_id=str(place_id),
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


async def search_places(
    *,
    sector: str,
    province_code: str | None = None,
    comune: str | None = None,
    radius_km: int = 30,
    keyword: str | None = None,
    limit: int = 60,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> list[ProspectorPlace]:
    """Run Places discovery for the /scoperta operator-driven flow.

    Returns a deduplicated list of ProspectorPlace results, capped at
    `limit`. Empty list on no results / no API key / bad parameters.

    Behavior:
      - sector → `includedPrimaryTypes` via places_to_sector.py
      - comune (or province_code) → geo anchor (lat/lng centroid)
      - radius_km clamped to [5, 50] (>50 would need zoning, deferred)
      - keyword present → uses Text Search (which supports textQuery)
        with a single primary type filter and locationBias circle.
        keyword absent → uses Nearby Search with multiple primary types.
    """
    key = api_key or settings.google_places_api_key
    if not key:
        log.error(
            "places_prospector.skip_no_key — GOOGLE_PLACES_API_KEY is not "
            "set; /scoperta will return empty. Set it on the API service."
        )
        return []

    if not sector:
        log.warning("places_prospector.empty_sector")
        return []

    included_types = included_types_for_sector(sector)
    if not included_types:
        log.warning(
            "places_prospector.no_included_types",
            sector=sector,
            note="add the sector to _SECTOR_TO_INCLUDED_TYPES in places_to_sector.py",
        )
        return []

    radius_km_clamped = max(5, min(radius_km or 30, 50))
    radius_m = radius_km_clamped * 1000

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)

    try:
        anchor = await resolve_anchor(
            comune=comune,
            province_code=province_code,
            client=client,
            api_key=key,
        )
        if anchor is None:
            log.warning(
                "places_prospector.anchor_unresolved",
                comune=comune,
                province=province_code,
            )
            return []
        lat, lng = anchor

        all_places: dict[str, ProspectorPlace] = {}

        if keyword and keyword.strip():
            # Text Search path: single primary type at a time, run once
            # per included_type up to the limit budget.
            per_type_max = max(5, min(limit, 20))
            for ptype in included_types:
                if len(all_places) >= limit:
                    break
                try:
                    raw = await _places_text_search(
                        text_query=keyword.strip(),
                        lat=lat,
                        lng=lng,
                        radius_m=radius_m,
                        included_type=ptype,
                        max_results=per_type_max,
                        client=client,
                        api_key=key,
                    )
                except (httpx.HTTPError, httpx.TimeoutException) as exc:
                    log.warning(
                        "places_prospector.text_call_error",
                        sector=sector,
                        ptype=ptype,
                        err=type(exc).__name__,
                    )
                    continue
                for raw_place in raw:
                    cand = _parse_place(raw_place)
                    if cand is None or cand.google_place_id in all_places:
                        continue
                    all_places[cand.google_place_id] = cand
                    if len(all_places) >= limit:
                        break
        else:
            # Nearby Search path: one call with all included_types in
            # `includedPrimaryTypes`, capped at 20 results per call.
            try:
                raw = await _places_nearby(
                    lat=lat,
                    lng=lng,
                    radius_m=radius_m,
                    included_types=included_types,
                    keyword=None,
                    max_results=min(limit, 20),
                    client=client,
                    api_key=key,
                )
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                log.warning(
                    "places_prospector.nearby_call_error",
                    sector=sector,
                    err=type(exc).__name__,
                )
                return []
            for raw_place in raw:
                cand = _parse_place(raw_place)
                if cand is None or cand.google_place_id in all_places:
                    continue
                all_places[cand.google_place_id] = cand

        log.info(
            "places_prospector.done",
            sector=sector,
            comune=comune,
            province=province_code,
            radius_km=radius_km_clamped,
            keyword=bool(keyword),
            results=len(all_places),
        )
        return list(all_places.values())[:limit]
    finally:
        if owns_client:
            await client.aclose()
