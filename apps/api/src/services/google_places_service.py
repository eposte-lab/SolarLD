"""Google Places API (New) client.

Wraps two endpoints used by HunterAgent in `b2b_precision` mode:

  POST https://places.googleapis.com/v1/places:searchNearby
  GET  https://places.googleapis.com/v1/places/{place_id}

The **"New" Places API** (v1, 2023+) is used — not the legacy
`findplacefromtext` / `nearbysearch` JSON v3. It accepts a JSON body
with `includedTypes`, `locationRestriction.circle` and a FieldMask
header that controls which fields come back (→ cost tier).

Pricing (2024, as-of docs):
  - Nearby Search (Basic SKU fields)         ≈ $0.032 / call
  - Nearby Search (Advanced SKU)             ≈ $0.050 / call
  - Place Details (Basic SKU, id/displayName) ≈ $0.017 / call

We default to Basic SKU everywhere — Hunter doesn't need ratings or
reviews, just name + coords + address + business_status. Reported in
cents through `api_usage_log` for monthly budget accounting.

The module is deliberately small: Hunter owns the orchestration
(pagination, dedupe by place_id, grid-cell loop). Here we just expose
low-level async calls with retries, typed projections, and a cost
constant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

PLACES_NEARBY_ENDPOINT = "https://places.googleapis.com/v1/places:searchNearby"
PLACES_DETAILS_ENDPOINT_TEMPLATE = "https://places.googleapis.com/v1/places/{place_id}"

# Cost in cents per call — Basic SKU. Kept as integers to match api_usage_log.
NEARBY_COST_PER_CALL_CENTS = 3  # ≈ $0.032, rounded up
DETAILS_COST_PER_CALL_CENTS = 2  # ≈ $0.017, rounded up

# Basic SKU field masks — minimum needed by Hunter.
_NEARBY_FIELD_MASK = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.location,"
    "places.businessStatus,"
    "places.types,"
    "places.primaryType"
)
_DETAILS_FIELD_MASK = (
    "id,"
    "displayName,"
    "formattedAddress,"
    "location,"
    "businessStatus,"
    "types,"
    "primaryType,"
    "websiteUri,"
    "internationalPhoneNumber,"
    "nationalPhoneNumber"
)

# Google enforces max 20 results/page for Nearby Search; 50 is the
# Advanced tier limit. We cap at 20 here (Basic).
NEARBY_MAX_RESULTS = 20


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PlacesApiError(Exception):
    """Non-retryable Places API error."""


class PlacesApiRateLimited(Exception):
    """Retryable 429 / 503."""


# ---------------------------------------------------------------------------
# Typed projections
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class PlaceSummary:
    """Projection of a single Place from Nearby Search (Basic SKU)."""

    place_id: str
    name: str
    address: str | None
    lat: float
    lng: float
    business_status: str | None  # OPERATIONAL | CLOSED_TEMPORARILY | CLOSED_PERMANENTLY
    types: tuple[str, ...]
    primary_type: str | None

    @property
    def is_operational(self) -> bool:
        # Missing status is interpreted as operational — Google occasionally
        # omits the field for long-established places.
        return self.business_status in (None, "OPERATIONAL")


@dataclass(slots=True, frozen=True)
class PlaceDetails:
    """Projection of Place Details (Basic SKU + websiteUri + phone)."""

    place_id: str
    name: str
    address: str | None
    lat: float
    lng: float
    business_status: str | None
    types: tuple[str, ...]
    primary_type: str | None
    website: str | None
    phone_international: str | None
    phone_national: str | None


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _parse_place_summary(p: dict[str, Any]) -> PlaceSummary | None:
    """Parse one item from `places[]`. Returns None on missing id/coords."""
    pid = p.get("id")
    loc = p.get("location") or {}
    lat = loc.get("latitude")
    lng = loc.get("longitude")
    if not pid or lat is None or lng is None:
        return None

    display = p.get("displayName") or {}
    name = display.get("text") if isinstance(display, dict) else str(display)

    return PlaceSummary(
        place_id=pid,
        name=name or "",
        address=p.get("formattedAddress"),
        lat=float(lat),
        lng=float(lng),
        business_status=p.get("businessStatus"),
        types=tuple(p.get("types") or ()),
        primary_type=p.get("primaryType"),
    )


def _parse_place_details(p: dict[str, Any]) -> PlaceDetails:
    """Parse `/places/{id}` response."""
    loc = p.get("location") or {}
    display = p.get("displayName") or {}
    name = display.get("text") if isinstance(display, dict) else str(display)

    return PlaceDetails(
        place_id=p.get("id", ""),
        name=name or "",
        address=p.get("formattedAddress"),
        lat=float(loc.get("latitude", 0.0) or 0.0),
        lng=float(loc.get("longitude", 0.0) or 0.0),
        business_status=p.get("businessStatus"),
        types=tuple(p.get("types") or ()),
        primary_type=p.get("primaryType"),
        website=p.get("websiteUri"),
        phone_international=p.get("internationalPhoneNumber"),
        phone_national=p.get("nationalPhoneNumber"),
    )


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception_type(PlacesApiRateLimited),
    reraise=True,
)
async def nearby_search(
    lat: float,
    lng: float,
    *,
    radius_m: float,
    included_types: list[str],
    max_results: int = NEARBY_MAX_RESULTS,
    language: str = "it",
    region: str = "IT",
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> list[PlaceSummary]:
    """Run a Nearby Search within `radius_m` of (lat, lng) filtered by
    `included_types` (Google Places type strings, e.g. 'supermarket').

    Returns up to `max_results` places (Basic SKU). Duplicates within
    the same call are absent by construction (Google dedupes on place_id).
    """
    key = api_key or settings.google_places_api_key
    if not key:
        raise PlacesApiError("GOOGLE_PLACES_API_KEY not configured")
    if not included_types:
        # Defensive: empty filter would match everything on the grid; caller
        # should have defaulted to ['establishment'].
        raise PlacesApiError("included_types must not be empty")

    body: dict[str, Any] = {
        "includedTypes": included_types,
        "maxResultCount": min(max_results, NEARBY_MAX_RESULTS),
        "languageCode": language,
        "regionCode": region,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": radius_m,
            }
        },
    }
    headers = {
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": _NEARBY_FIELD_MASK,
        "Content-Type": "application/json",
    }

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=20.0)

    try:
        resp = await client.post(PLACES_NEARBY_ENDPOINT, json=body, headers=headers)
    finally:
        if owns_client:
            await client.aclose()

    if resp.status_code in (429, 503):
        log.warning("places_api_rate_limited", extra={"status": resp.status_code, "lat": lat, "lng": lng})
        raise PlacesApiRateLimited(f"status={resp.status_code}")
    if resp.status_code >= 400:
        log.error(
            "places_api_error",
            extra={"status": resp.status_code, "body": resp.text[:500]},
        )
        raise PlacesApiError(f"status={resp.status_code} body={resp.text[:200]}")

    data = resp.json()
    raw_places = data.get("places") or []
    out: list[PlaceSummary] = []
    for p in raw_places:
        parsed = _parse_place_summary(p)
        if parsed is not None:
            out.append(parsed)
    return out


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception_type(PlacesApiRateLimited),
    reraise=True,
)
async def place_details(
    place_id: str,
    *,
    language: str = "it",
    region: str = "IT",
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> PlaceDetails:
    """Fetch full details for a single place (including website/phone).

    Only called on place_ids that passed the Solar-scan technical
    filters — keeps Details spend proportional to lead candidates.
    """
    key = api_key or settings.google_places_api_key
    if not key:
        raise PlacesApiError("GOOGLE_PLACES_API_KEY not configured")

    url = PLACES_DETAILS_ENDPOINT_TEMPLATE.format(place_id=place_id)
    headers = {
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": _DETAILS_FIELD_MASK,
    }
    params = {"languageCode": language, "regionCode": region}

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)

    try:
        resp = await client.get(url, headers=headers, params=params)
    finally:
        if owns_client:
            await client.aclose()

    if resp.status_code in (429, 503):
        log.warning("places_details_rate_limited", extra={"status": resp.status_code})
        raise PlacesApiRateLimited(f"status={resp.status_code}")
    if resp.status_code == 404:
        raise PlacesApiError(f"place not found: {place_id}")
    if resp.status_code >= 400:
        log.error(
            "places_details_error",
            extra={"status": resp.status_code, "body": resp.text[:500]},
        )
        raise PlacesApiError(f"status={resp.status_code} body={resp.text[:200]}")

    return _parse_place_details(resp.json())


# ---------------------------------------------------------------------------
# Test helpers — feed fixture JSON without hitting the network.
# ---------------------------------------------------------------------------


def parse_nearby_payload(payload: dict[str, Any]) -> list[PlaceSummary]:
    """Public alias for unit tests: parse a captured Nearby response."""
    out: list[PlaceSummary] = []
    for p in payload.get("places") or []:
        parsed = _parse_place_summary(p)
        if parsed is not None:
            out.append(parsed)
    return out


def parse_details_payload(payload: dict[str, Any]) -> PlaceDetails:
    """Public alias for unit tests: parse a captured Details response."""
    return _parse_place_details(payload)
