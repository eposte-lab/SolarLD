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

import asyncio
from dataclasses import dataclass
from typing import Any

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


# ---------------------------------------------------------------------------
# BIC Stage 2 — multi-query
# ---------------------------------------------------------------------------


# ATECO prefix → keyword hint to inject into a Places query. Built from
# Italian ATECO 2007 sector descriptions; we deliberately keep this
# small and high-precision (over-tagged keywords cause false matches
# against generic businesses with the same word in their description).
_ATECO_KEYWORD_HINTS = {
    "01": "agricola",
    "10": "alimentare",
    "13": "tessile",
    "14": "abbigliamento",
    "20": "chimica",
    "22": "plastica",
    "23": "ceramica",
    "24": "metallurgica",
    "25": "metalmeccanica",
    "27": "elettrotecnica",
    "28": "macchinari",
    "29": "automotive",
    "31": "mobili",
    "33": "manutenzione",
    "41": "edilizia",
    "43": "impianti",
    "45": "concessionaria",
    "46": "commercio ingrosso",
    "47": "commercio dettaglio",
    "49": "logistica trasporto",
    "52": "magazzini logistica",
    "55": "hotel",
    "56": "ristorazione",
    "62": "informatica",
    "70": "consulenza",
    "85": "scuola",
    "86": "sanità",
}


def _strip_corporate_suffix(name: str) -> str:
    """Remove Italian corporate suffixes (S.P.A., S.R.L., …) for query building."""
    out = name.strip()
    for suffix in (
        " S.P.A.", " S.p.A.", " SPA", " s.p.a.", " spa",
        " S.R.L.", " S.r.l.", " SRL", " s.r.l.", " srl",
        " S.A.S.", " S.a.s.", " SAS", " s.a.s.", " sas",
        " S.N.C.", " S.n.c.", " SNC", " s.n.c.", " snc",
        " & C.", " & C", " soc. coop.", " soc coop",
    ):
        if out.endswith(suffix):
            out = out[: -len(suffix)].strip()
    return out


def build_multi_query_variants(
    legal_name: str,
    *,
    city: str | None = None,
    province: str | None = None,
    ateco_code: str | None = None,
    ateco_description: str | None = None,
) -> list[str]:
    """Build 4-6 differently-formatted Places queries for one company.

    The Places "FindPlaceFromText" API ranks by a synthetic relevance
    score that's heavily affected by exact name match. By probing
    multiple formulations of the same company name we boost recall
    when:
      * the company is registered as "Multilog S.P.A." but Google's
        business listing shows just "Multilog";
      * the listing mentions only the trade name (e.g. "Multilog
        Logistics") rather than the legal name;
      * the company is in a Z.I. but Google's place is anchored on the
        nearby town rather than the agglomerato.

    Each unique ``place_id`` returned by the variants ends up as one
    BuildingCandidate; multiple variants converging on the same place
    naturally pool weight in the voter.
    """
    base = _strip_corporate_suffix(legal_name)
    first_token = base.split()[0] if base.split() else legal_name
    queries: list[str] = []

    def _add(q: str) -> None:
        q = " ".join(q.split())  # collapse whitespace
        if q and q not in queries:
            queries.append(q)

    # Variant 1 — exact legal name (kept verbatim so a perfectly listed
    # SPA still hits even when subsequent variants would lose a token).
    _add(legal_name)

    # Variant 2 — stripped, plus city when known.
    _add(f"{base} {city or ''}".strip())

    # Variant 3 — stripped, plus province (catches companies whose
    # listing is anchored on the province capital rather than the comune).
    if province:
        _add(f"{base} {province}")

    # Variant 4 — first-token + city. Helpful when the legal name is
    # multi-word but the trade name is just the first token.
    if first_token != base:
        _add(f"{first_token} {city or ''}".strip())

    # Variant 5 — ATECO keyword hint. Helpful for generic names ("Sole
    # Energy" → "Sole Energy logistica") to disambiguate against random
    # POIs sharing the same word.
    keyword: str | None = None
    if ateco_code:
        keyword = _ATECO_KEYWORD_HINTS.get(str(ateco_code).strip()[:2])
    if not keyword and ateco_description:
        # Take the first content word of the ATECO description as a
        # fallback. Skip the leading article if any.
        words = [w for w in ateco_description.split() if len(w) > 3]
        keyword = words[0].lower() if words else None
    if keyword:
        _add(f"{base} {keyword}")

    # Cap at 6 variants — anything beyond this rarely surfaces a new
    # place_id and just multiplies cost.
    return queries[:6]


@dataclass(slots=True)
class _PlaceFromVariant:
    place_id: str
    lat: float
    lng: float
    formatted_address: str | None
    display_name: str | None
    confidence: float
    matched_variants: list[str]


async def search_text_multi_query(
    *,
    legal_name: str,
    city: str | None = None,
    province: str | None = None,
    ateco_code: str | None = None,
    ateco_description: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    location_bias_centre: tuple[float, float] | None = None,
    api_key: str | None = None,
) -> "list[Any]":
    """Stage 2 of the BIC: fan out 4-6 query variants → BuildingCandidate list.

    Returns a list of ``BuildingCandidate`` (one per unique ``place_id``)
    with ``weight`` proportional to how many variants converged on it
    *and* the per-result Places confidence. Empty list on no API key,
    no hits, or any failure.

    ``location_bias_centre`` (lat, lng) — when provided, restricts the
    search to ~5 km around that point so a generic name like
    "Multilog" doesn't pull a national chain match. Typically the
    caller passes the legacy resolver's coordinates here so we stay
    anchored on the right industrial zone.
    """
    # Local import to avoid the circular building_identification → here.
    from . import building_identification as bic

    queries = build_multi_query_variants(
        legal_name=legal_name,
        city=city,
        province=province,
        ateco_code=ateco_code,
        ateco_description=ateco_description,
    )
    if not queries:
        return []

    # Group by place_id so duplicates from different variants pool
    # their evidence into a single BuildingCandidate.
    by_place: dict[str, _PlaceFromVariant] = {}

    async def _run_one(query: str) -> None:
        try:
            res = await search_text(
                query,
                client=http_client,
                api_key=api_key,
            )
        except GooglePlacesError as exc:
            log.warning(
                "places_multi.error",
                query=query[:80],
                err=str(exc)[:120],
            )
            return
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "places_multi.unexpected",
                query=query[:80],
                err=type(exc).__name__,
            )
            return

        if res is None or not res.place_id:
            return

        # Drop hits that fall too far from the location bias — the
        # Places API itself doesn't currently honour locationBias on
        # text search the way it does on findPlaceFromText, so we
        # post-filter to avoid pulling in a same-named POI from a
        # different region.
        if location_bias_centre is not None:
            from .osm_building_service import _haversine_m  # reuse helper

            d = _haversine_m(
                location_bias_centre[0],
                location_bias_centre[1],
                res.lat,
                res.lng,
            )
            if d > 5_000:
                log.info(
                    "places_multi.outside_bias",
                    query=query[:80],
                    distance_m=int(d),
                )
                return

        existing = by_place.get(res.place_id)
        if existing is None:
            by_place[res.place_id] = _PlaceFromVariant(
                place_id=res.place_id,
                lat=res.lat,
                lng=res.lng,
                formatted_address=res.formatted_address,
                display_name=res.display_name,
                confidence=res.confidence,
                matched_variants=[query],
            )
        else:
            existing.matched_variants.append(query)
            existing.confidence = max(existing.confidence, res.confidence)

    # Fire all variants in parallel — Places handles a few concurrent
    # requests fine and total wall-clock for 6 calls drops from ~3s to
    # ~0.5s.
    await asyncio.gather(*(_run_one(q) for q in queries), return_exceptions=False)

    # Project into BuildingCandidates. Weight = base 0.3 per match,
    # boosted by per-result confidence. A place that surfaced in 3+
    # variants and has formatted_address with a CAP gets up to ~0.9.
    out: "list[Any]" = []
    for hit in by_place.values():
        # Each variant adds ~0.2 weight (capped to avoid runaway).
        variant_score = min(0.6, 0.2 * len(hit.matched_variants))
        weight = variant_score + 0.3 * hit.confidence  # 0.0..0.84 typical
        out.append(
            bic.BuildingCandidate(
                lat=hit.lat,
                lng=hit.lng,
                weight=round(weight, 3),
                source=f"places_x{len(hit.matched_variants)}",
                polygon_geojson=None,
                metadata={
                    "place_id": hit.place_id,
                    "address": hit.formatted_address,
                    "display_name": hit.display_name,
                    "matched_variants": hit.matched_variants,
                    "places_confidence": hit.confidence,
                },
            )
        )
    log.info(
        "places_multi.completed",
        legal_name=legal_name,
        n_queries=len(queries),
        n_unique_places=len(out),
    )
    return out
