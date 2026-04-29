"""Google Places API (New) — text search for company operating sites.

Used by ``operating_site_resolver`` as the third tier of the rooftop
identification cascade (Atoka → website → **Google Places** → Mapbox
HQ centroid). When Atoka has no operating-site location and the
company website does not publish a parseable address, we fall back to
Google's Places Text Search: it indexes business listings under their
trade name, which often surfaces the actual building even when the
legal HQ on the chamber-of-commerce filing is a notary's office.

Cost
----
The Places API (New) bills per request based on the field mask. We
query only ``places.formattedAddress`` and ``places.location`` (the
"Essentials" field set), which lands at $0.005 / request. Rounded up
to ``2`` cents per call for accounting in `ctx.costs["google_places"]`
so we always over-attribute rather than under-attribute spend.

Rate limits
-----------
Default project quota is 600 QPM; we add a 5-second timeout +
3-attempt tenacity retry on transient 5xx so a flaky Google response
does not crash the pipeline. The cascade caller is expected to time
out the whole resolver gracefully if every tier fails.

Provenance
----------
Returns a ``PlacesResult`` carrying the formatted address, lat/lng
and the Google place ``id`` so we can store it on the subject row
alongside ``sede_operativa_source = 'google_places'`` for audit and
later geocoding refresh.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)


# Places API (New) endpoint — note the v1 path; the legacy
# ``maps.googleapis.com/maps/api/place/textsearch/json`` is being
# deprecated in favour of this one.
PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Field mask is sent as a header; we only ask for what we use to keep
# us in the cheaper pricing tier.
PLACES_FIELD_MASK = "places.id,places.formattedAddress,places.location,places.displayName"

# Cost recorded against ``ctx.costs["google_places"]`` per call. Two
# cents covers the field mask we use plus a margin for the bundled
# atmosphere fields that occasionally leak through.
PLACES_COST_PER_CALL_CENTS = 2


class GooglePlacesError(Exception):
    """Raised when Google Places returns an unrecoverable error."""


@dataclass(slots=True)
class PlacesResult:
    """Top hit from a Places text search.

    ``confidence`` is a synthesized 0..1 number — Google's Places API
    does not expose a relevance score directly, so we derive one from
    the rank position (Google sorts by their internal relevance) and
    whether the formatted address contains a recognisable Italian
    postcode. The resolver uses this to decide whether to accept the
    answer or fall through to Mapbox HQ.
    """

    place_id: str
    formatted_address: str
    lat: float
    lng: float
    display_name: str | None
    confidence: float


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def search_text(
    query: str,
    *,
    region_code: str = "IT",
    language_code: str = "it",
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> PlacesResult | None:
    """Run a Places "text search" and return the top business hit.

    Parameters
    ----------
    query:
        Free-text search string. The resolver builds this from
        ``f"{legal_name} {hq_city or ''} {hq_province or ''}"`` so a
        narrow company name plus a city anchor is provided whenever
        possible.
    region_code, language_code:
        Bias the result towards Italy / Italian — Google heavily
        favours the user's locale otherwise and an Italian SME named
        "Multilog" can be drowned by a US logistics chain.
    client:
        Optional shared ``httpx.AsyncClient``. When ``None``, a
        transient client is created and closed inline.
    api_key:
        Override for tests. Defaults to ``settings.google_places_api_key``.

    Returns
    -------
    ``None`` when:
      * No API key is configured (no error — operator hasn't enabled
        the Places dependency yet).
      * Empty / whitespace query.
      * Google returns zero results.
    """

    key = api_key or settings.google_places_api_key
    if not key:
        log.debug("google_places.skip_no_key")
        return None
    if not query or not query.strip():
        return None

    payload = {
        "textQuery": query.strip(),
        "regionCode": region_code,
        "languageCode": language_code,
        # Restrict to top hit to stay in the cheap pricing bucket.
        "pageSize": 1,
    }

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": PLACES_FIELD_MASK,
    }

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=5.0)
    try:
        resp = await client.post(PLACES_TEXT_SEARCH_URL, json=payload, headers=headers)
    finally:
        if owns_client:
            await client.aclose()

    if resp.status_code == 403:
        # Most common production failure: Places API not enabled on
        # the GCP project, or the key has IP/referrer restrictions.
        # Surface as an error so the resolver can log and fall through.
        raise GooglePlacesError(f"places_403: {resp.text[:200]}")
    if resp.status_code >= 400:
        raise GooglePlacesError(
            f"places_http_{resp.status_code}: {resp.text[:200]}"
        )

    data = resp.json() or {}
    places = data.get("places") or []
    if not places:
        log.debug("google_places.no_results", query=query[:80])
        return None

    top = places[0]
    location = top.get("location") or {}
    lat = location.get("latitude")
    lng = location.get("longitude")
    if lat is None or lng is None:
        return None

    formatted_address = top.get("formattedAddress") or ""
    display = top.get("displayName") or {}
    display_text = display.get("text") if isinstance(display, dict) else None

    # Confidence heuristic: start at 0.6 (Google ranked it first), bump
    # to 0.8 if the formatted address contains an Italian 5-digit CAP
    # (anchors the result to a real postal address rather than a POI
    # blob).
    confidence = 0.6
    if any(part.strip().isdigit() and len(part.strip()) == 5
           for part in formatted_address.split(",")):
        confidence = 0.8

    return PlacesResult(
        place_id=str(top.get("id") or ""),
        formatted_address=formatted_address,
        lat=float(lat),
        lng=float(lng),
        display_name=display_text,
        confidence=confidence,
    )
