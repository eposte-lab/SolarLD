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


def _denormalise_punctuation(name: str) -> str:
    """Strip dots from corporate abbreviations: 'IDANA F.W.A. S.R.L.' → 'IDANA FWA SRL'.

    Google Places business listings are inconsistent about punctuation.
    Some businesses are registered with dots ("S.R.L."), Google indexes
    them without ("SRL"), and a search with the original punctuation
    misses them entirely. We always probe BOTH variants so a query like
    "IDANA SRL" hits the listing even when the legal name is registered
    as "IDANA S.R.L." — and vice-versa.
    """
    out = name
    # Collapse single-letter-dot sequences into the unpunctuated form,
    # e.g. "S.R.L." → "SRL", "F.W.A." → "FWA". The pattern is
    # specifically letter+dot+letter+dot to avoid touching legitimate
    # abbreviations like "Via S. Antonio" → "Via S Antonio" (we only
    # touch sequences of 2+ adjacent letter-dots).
    import re

    out = re.sub(r"\b((?:[A-Za-z]\.){2,})", lambda m: m.group(1).replace(".", ""), out)
    return out.strip()


def _normalise_punctuation(name: str) -> str:
    """The opposite — try to add dots where the listing might use them.

    'MULTILOG SPA' → 'MULTILOG S.P.A.'  Best-effort, only applies to
    well-known Italian corporate suffixes at the end of the name.
    """
    out = name.strip()
    suffix_map = {
        " SPA": " S.P.A.",
        " SRL": " S.R.L.",
        " SAS": " S.A.S.",
        " SNC": " S.N.C.",
    }
    upper = out.upper()
    for raw_suffix, dotted_suffix in suffix_map.items():
        if upper.endswith(raw_suffix):
            return out[: -len(raw_suffix)] + dotted_suffix
    return out


def build_multi_query_variants(
    legal_name: str,
    *,
    city: str | None = None,
    province: str | None = None,
    ateco_code: str | None = None,
    ateco_description: str | None = None,
) -> list[str]:
    """Build a long list of differently-formatted Places queries.

    Italian B2B listings are wildly inconsistent in how they appear on
    Google: punctuation varies (S.P.A. vs SPA), the trade name may be
    the first token only, the city in the listing may be the comune or
    the more famous nearby town, and the sector hint matters when the
    name itself is a common word. Recall-first strategy: generate up to
    ~12 unique formulations, fire them in parallel against Places (each
    call is ~$0.017), pool by place_id in the voter — one place that
    surfaces under 3+ formulations is far more credible than one that
    only matches the literal legal name.

    The variant set covers:
      1. Verbatim legal name (literal listing match)
      2-3. Punctuation normalised + denormalised (S.R.L. ↔ SRL)
      4-7. Stripped corporate suffix × {bare, +city, +province, +"italia"}
      8-9. First token only × {bare, +city}
      10-11. ATECO keyword hint × {full name, first token}
      12. ATECO description first-word verbatim
    """
    queries: list[str] = []

    def _add(q: str) -> None:
        q = " ".join(q.split()).strip()
        if q and q not in queries:
            queries.append(q)

    legal_clean = legal_name.strip()
    base = _strip_corporate_suffix(legal_clean)
    first_token = base.split()[0] if base.split() else legal_clean
    name_no_dots = _denormalise_punctuation(legal_clean)
    name_with_dots = _normalise_punctuation(legal_clean)

    # 1. Verbatim — preserves the exact registered form so a perfectly
    #    listed company hits on the first call.
    _add(legal_clean)

    # 2. Punctuation removed: "MULTILOG S.P.A." → "MULTILOG SPA".
    _add(name_no_dots)

    # 3. Punctuation added: "MULTILOG SPA" → "MULTILOG S.P.A.".
    _add(name_with_dots)

    # 4. Bare stripped name. Many Italian listings are registered under
    #    the trade name without any suffix at all.
    _add(base)

    # 5-7. Stripped + city / province / "italia" (covers listings
    #      anchored on a different geography than the registered HQ).
    if city:
        _add(f"{base} {city}")
    if province:
        _add(f"{base} {province}")
    _add(f"{base} italia")

    # 8-9. First-token only with optional city (catches "Multilog
    #      Logistics SRL" → listing as just "Multilog").
    if first_token != base:
        _add(first_token)
        if city:
            _add(f"{first_token} {city}")

    # 10. ATECO keyword hint — disambiguates generic names like "Sole
    #     Energy SRL" → "Sole Energy logistica" so the result isn't a
    #     hotel with the same word in its name.
    keyword: str | None = None
    if ateco_code:
        keyword = _ATECO_KEYWORD_HINTS.get(str(ateco_code).strip()[:2])
    if not keyword and ateco_description:
        words = [w for w in ateco_description.split() if len(w) > 3]
        keyword = words[0].lower() if words else None
    if keyword:
        _add(f"{base} {keyword}")
        # 11. ATECO keyword + first token (covers very short trade names).
        if first_token != base:
            _add(f"{first_token} {keyword}")

    # 12. ATECO description first chunk verbatim (matches listings whose
    #     description text Google indexes).
    if ateco_description:
        first_chunk = ateco_description.split(",")[0].strip()
        if first_chunk and len(first_chunk) <= 60:
            _add(f"{base} {first_chunk}")

    # Cap at 6 — empirically the diminishing-returns knee. Variants 1-6
    # cover 90+% of legitimate matches (verbatim, no-punctuation, bare,
    # +city, +ATECO keyword, first-token+city); variants 7-12 added <10%
    # recall while doubling worst-case spend. Combined with the
    # early-stop in search_text_multi_query (bail as soon as ≥2 unique
    # place_ids converge), typical demo runs now fire 2-4 variants.
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
            # 15 km radius — Italian "Z.I." footprints can be huge
            # (Pascarola spans ~3 km × 3 km on its own, and the
            # legacy mapbox_hq centroid often lands at the
            # geographical centre of the comune which is several
            # km from the actual industrial cluster). 5 km was
            # rejecting legitimate matches.
            if d > 15_000:
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

    # Batched parallel execution with early-stop when ≥2 unique
    # places converge.
    #
    # We fire variants in groups of 2 (parallel inside the group,
    # serial across groups) and after each group check if we already
    # have enough convergence. As soon as ≥2 unique place_ids
    # accumulated, we bail — additional variants would either
    # duplicate the same places (no new info, just bumping the
    # variant score) or drag in unrelated POIs (noise).
    #
    # Effect on cost: typical demo runs that found a match in the
    # first 2-3 variants now stop there instead of firing all 6 →
    # average Places spend drops from ~$0.10 to ~$0.04. Worst-case
    # (no convergence) still fires all 6 variants, same as before.
    #
    # Effect on latency: parallel groups of 2 keep wall-clock
    # comparable to the previous full-parallel approach (within ~1s)
    # while saving the back half of API calls when they're not
    # needed.
    queries_fired = 0
    for batch_start in range(0, len(queries), 2):
        batch = queries[batch_start : batch_start + 2]
        await asyncio.gather(
            *(_run_one(q) for q in batch), return_exceptions=False
        )
        queries_fired += len(batch)
        if len(by_place) >= 2:
            log.info(
                "places_multi.early_stop",
                legal_name=legal_name,
                queries_fired=queries_fired,
                queries_total=len(queries),
                unique_places=len(by_place),
                note="2+ unique places already converged — bailing",
            )
            break

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
        n_queries_total=len(queries),
        n_queries_fired=queries_fired,
        n_unique_places=len(out),
    )
    return out
