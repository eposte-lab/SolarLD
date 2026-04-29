"""Operating-site (sede operativa) resolver — Sprint Demo Polish Phase B.

The rooftop render in our outreach creative is the single most
visceral signal a recipient sees: a satellite tile of the actual
building with solar panels painted on top. Until now we had a single
source of truth for the (lat, lng) that drives that render — the
Atoka HQ address forward-geocoded through Mapbox. That works for
companies whose registered office is the same as their warehouse
(SMEs working from a single location), but breaks badly when the
chamber-of-commerce filing points at:

  * a notary's address (tax-residence pattern for small SRLs);
  * an accountant's office (very common for newly registered firms);
  * the centroid of an industrial cluster (Atoka returns the cluster
    coordinates when the company has multiple sites and no canonical
    HQ flag);
  * a P.O. box in a different city from where they actually operate.

In all of these cases we ended up rendering panels on the wrong
building, which is at best embarrassing and at worst kills our
credibility on the demo call. This resolver fixes that with a
4-tier cascade — the same chain runs in production *and* in the
``/v1/demo/test-pipeline`` flow, so what the customer sees during the
sales call is what real leads will get.

Cascade order
-------------

  1. **Atoka** (high confidence, zero marginal cost) — pick the
     ``locations[]`` entry whose ``type`` is one of
     ``{operating, secondary, production, branch}``. Already
     populated by ``AtokaProfile.sede_operativa_*`` when present.

  2. **Website scrape + Mapbox forward_geocode** (medium confidence,
     zero marginal cost) — pull the address out of a schema.org
     ``Organization`` block, an HTML ``<address>`` tag or an inline
     Italian-street regex on the company website. Validate by
     forward-geocoding through Mapbox and accepting only relevance
     ≥ 0.7.

  3. **Google Places API** (medium confidence, ~$0.005/call) — text
     search for ``"{legal_name} {city} {province}"``. Google's
     business index is much richer than Atoka for service-sector
     SMEs that don't bother updating their Camera di Commercio entry.

  4. **Mapbox HQ centroid** (low confidence, status quo) — last
     resort: forward-geocode the legal HQ address. We mark the
     resulting record with ``confidence='low'`` so the dashboard
     can surface a "centroide HQ" badge and ops know the lead
     deserves a manual check before sending.

Failure mode
------------
``resolve_operating_site`` always returns an ``OperatingSite``. When
nothing in the cascade succeeds the returned object has
``lat is None and lng is None`` and ``source = 'unresolved'`` — the
caller is responsible for blocking outreach (legacy paths in
``level4_solar_gate.py`` already early-return when coords are missing,
so behaviour is preserved).

Tests
-----
See ``apps/api/tests/test_operating_site_resolver.py``. Each tier is
exercised independently with the others mocked out, plus an
end-to-end "all sources fail" path that verifies we land on the
``unresolved`` sentinel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from ..core.logging import get_logger
from ..services.italian_business_service import AtokaProfile
from . import email_extractor, google_places_service, mapbox_service

log = get_logger(__name__)


# Confidence buckets surfaced to the dashboard. Resolver maps source
# to a bucket so callers don't need to know the cascade tiers.
HIGH_CONFIDENCE = "high"
MEDIUM_CONFIDENCE = "medium"
LOW_CONFIDENCE = "low"

# Minimum Mapbox forward_geocode relevance for an accepted match in
# tier 2. Below this we consider the geocode too ambiguous to trust
# (Mapbox happily resolves "Via Roma" alone to half the comuni in
# Italy at relevance ≈ 0.4).
MIN_GEOCODE_RELEVANCE = 0.7


@dataclass(slots=True)
class OperatingSite:
    """Resolved operating-site record.

    Maps onto the ``subjects.sede_operativa_*`` columns added in
    migration 0080. ``source`` is the value to write into
    ``sede_operativa_source`` so the dashboard can render the
    provenance badge ("Atoka / Sito web / Google Places / Centroide
    HQ").
    """

    lat: float | None
    lng: float | None
    address: str | None
    cap: str | None
    city: str | None
    province: str | None
    source: str             # 'atoka' | 'website_scrape' | 'google_places' | 'mapbox_hq' | 'unresolved'
    confidence: str         # 'high' | 'medium' | 'low' | 'none'

    @classmethod
    def empty(cls) -> "OperatingSite":
        return cls(
            lat=None,
            lng=None,
            address=None,
            cap=None,
            city=None,
            province=None,
            source="unresolved",
            confidence="none",
        )

    @property
    def has_coords(self) -> bool:
        return self.lat is not None and self.lng is not None


# Type aliases for the injectable dependencies — kept here so tests
# can patch them without monkey-patching module globals.
_ScanWebsiteCallable = Callable[..., Awaitable[Any]]
_ForwardGeocodeCallable = Callable[..., Awaitable[Any]]
_PlacesSearchCallable = Callable[..., Awaitable[Any]]


async def resolve_operating_site(
    *,
    profile: AtokaProfile | None,
    legal_name: str,
    website_domain: str | None,
    hq_address: str | None,
    hq_city: str | None,
    hq_province: str | None,
    http_client: httpx.AsyncClient | None = None,
    cost_meter: dict[str, int] | None = None,
    # Test seams. Default to the real services.
    scan_website: _ScanWebsiteCallable = email_extractor.scan_website_for_address,
    forward_geocode: _ForwardGeocodeCallable = mapbox_service.forward_geocode,
    places_search: _PlacesSearchCallable = google_places_service.search_text,
) -> OperatingSite:
    """Run the 4-tier cascade and return the best operating-site coords.

    Parameters
    ----------
    profile:
        Atoka profile for tier 1. Pass ``None`` when Atoka was not
        consulted (e.g. demo path with mock enrichment + a VAT not in
        the mock table).
    legal_name:
        Used as the search query in tier 3 (Google Places).
    website_domain:
        Domain only ("example.com"), no scheme. Tier 2 input.
    hq_address, hq_city, hq_province:
        Legal HQ — tier 4 fallback. ``hq_city`` is also passed into
        the Google Places query when present.
    http_client:
        Shared async client. Each tier creates its own when ``None``.
    cost_meter:
        Optional dict that the resolver decorates with per-source
        costs (in cents). Mutated in place.

    Returns
    -------
    Always returns an ``OperatingSite``. When no source produced
    coordinates the result is ``OperatingSite.empty()`` with
    ``source='unresolved'``.
    """

    if cost_meter is None:
        cost_meter = {}

    # ── Tier 1: Atoka sede operativa ────────────────────────────
    if (
        profile is not None
        and profile.sede_operativa_lat is not None
        and profile.sede_operativa_lng is not None
    ):
        log.info(
            "operating_site.tier1_atoka_hit",
            legal_name=legal_name,
            address=profile.sede_operativa_address,
        )
        return OperatingSite(
            lat=profile.sede_operativa_lat,
            lng=profile.sede_operativa_lng,
            address=profile.sede_operativa_address,
            cap=profile.sede_operativa_cap,
            city=profile.sede_operativa_city,
            province=profile.sede_operativa_province,
            source="atoka",
            confidence=HIGH_CONFIDENCE,
        )

    # ── Tier 2: Website scrape → Mapbox forward_geocode ─────────
    if website_domain:
        try:
            scraped = await scan_website(website_domain, http_client=http_client)
        except Exception as exc:  # noqa: BLE001 - tier failures must not break the cascade
            log.warning(
                "operating_site.tier2_scrape_error",
                domain=website_domain,
                err=str(exc)[:160],
            )
            scraped = None

        if scraped is not None and scraped.address:
            try:
                geocoded = await forward_geocode(
                    scraped.as_geocode_query(),
                    client=http_client,
                    min_relevance=MIN_GEOCODE_RELEVANCE,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "operating_site.tier2_geocode_error",
                    err=str(exc)[:160],
                )
                geocoded = None

            if geocoded is not None:
                log.info(
                    "operating_site.tier2_website_hit",
                    legal_name=legal_name,
                    relevance=geocoded.relevance,
                    strategy=scraped.source_strategy,
                )
                return OperatingSite(
                    lat=geocoded.lat,
                    lng=geocoded.lng,
                    address=geocoded.address or scraped.address,
                    cap=geocoded.cap or scraped.cap,
                    city=geocoded.comune or scraped.city,
                    province=geocoded.provincia or scraped.province,
                    source="website_scrape",
                    confidence=MEDIUM_CONFIDENCE,
                )

    # ── Tier 3: Google Places API text search ───────────────────
    places_query_parts = [legal_name or ""]
    if hq_city:
        places_query_parts.append(hq_city)
    if hq_province:
        places_query_parts.append(hq_province)
    places_query = " ".join(p for p in places_query_parts if p).strip()
    if places_query:
        try:
            place = await places_search(places_query, client=http_client)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "operating_site.tier3_places_error",
                query=places_query[:80],
                err=str(exc)[:160],
            )
            place = None

        if place is not None:
            cost_meter["google_places"] = (
                cost_meter.get("google_places", 0)
                + google_places_service.PLACES_COST_PER_CALL_CENTS
            )
            log.info(
                "operating_site.tier3_places_hit",
                legal_name=legal_name,
                place_id=place.place_id,
                confidence=place.confidence,
            )
            return OperatingSite(
                lat=place.lat,
                lng=place.lng,
                address=place.formatted_address,
                cap=None,
                city=hq_city,
                province=hq_province,
                source="google_places",
                confidence=MEDIUM_CONFIDENCE,
            )

    # ── Tier 4: Mapbox HQ centroid (status quo, low confidence) ─
    if hq_address:
        try:
            geocoded = await forward_geocode(hq_address, client=http_client)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "operating_site.tier4_hq_geocode_error",
                err=str(exc)[:160],
            )
            geocoded = None

        if geocoded is not None:
            log.info(
                "operating_site.tier4_hq_fallback",
                legal_name=legal_name,
                relevance=geocoded.relevance,
            )
            return OperatingSite(
                lat=geocoded.lat,
                lng=geocoded.lng,
                address=geocoded.address or hq_address,
                cap=geocoded.cap,
                city=geocoded.comune or hq_city,
                province=geocoded.provincia or hq_province,
                source="mapbox_hq",
                confidence=LOW_CONFIDENCE,
            )

    log.warning(
        "operating_site.unresolved",
        legal_name=legal_name,
        had_profile=profile is not None,
        had_website=bool(website_domain),
        had_hq=bool(hq_address),
    )
    return OperatingSite.empty()
