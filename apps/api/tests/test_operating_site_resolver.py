"""Tier-by-tier tests for the operating-site cascade resolver.

The resolver itself is small but feeds three downstream HTTP services
(email_extractor, mapbox_service, google_places_service) — to avoid
flakiness, every test injects async stubs via the resolver's
``scan_website`` / ``forward_geocode`` / ``places_search`` keyword
arguments. Each test exercises exactly one tier so a regression in
one source does not silently mask another.

Coverage target:
  * Tier 1 — Atoka sede operativa fast-path
  * Tier 2 — website scrape + Mapbox forward_geocode
  * Tier 3 — Google Places fallback
  * Tier 4 — Mapbox HQ centroid (status quo)
  * Unresolved sentinel when every tier fails
  * Cost meter increments correctly for tier 3
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.services.italian_business_service import AtokaProfile
from src.services.operating_site_resolver import (
    HIGH_CONFIDENCE,
    LOW_CONFIDENCE,
    MEDIUM_CONFIDENCE,
    OperatingSite,
    resolve_operating_site,
)


# ---------------------------------------------------------------------------
# Fixtures and stubs
# ---------------------------------------------------------------------------


@dataclass
class _StubGeocode:
    lat: float
    lng: float
    address: str | None = None
    cap: str | None = None
    comune: str | None = None
    provincia: str | None = None
    relevance: float = 0.9


@dataclass
class _StubScraped:
    address: str
    cap: str | None = None
    city: str | None = None
    province: str | None = None
    confidence: float = 0.7
    source_strategy: str = "json_ld"

    def as_geocode_query(self) -> str:
        parts = [self.address]
        if self.cap:
            parts.append(self.cap)
        if self.city:
            parts.append(self.city)
        return " ".join(parts)


@dataclass
class _StubPlace:
    place_id: str
    formatted_address: str
    lat: float
    lng: float
    display_name: str | None = None
    confidence: float = 0.8


def _profile(**overrides) -> AtokaProfile:
    """Build a minimal AtokaProfile with optional sede_operativa coords."""
    base: dict = dict(
        vat_number="01234567890",
        legal_name="Stub Spa",
        ateco_code=None,
        ateco_description=None,
        yearly_revenue_cents=None,
        employees=None,
        website_domain=None,
        decision_maker_name=None,
        decision_maker_role=None,
        linkedin_url=None,
        phone=None,
        hq_address="Via Roma 1, 00100 Roma",
        hq_cap="00100",
        hq_city="Roma",
        hq_province="RM",
        hq_lat=41.9,
        hq_lng=12.5,
    )
    base.update(overrides)
    return AtokaProfile(**base)


async def _no_call(*_args, **_kwargs):
    raise AssertionError("This stub should not have been invoked")


# ---------------------------------------------------------------------------
# Tier 1 — Atoka sede operativa
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier1_atoka_sede_operativa_short_circuits() -> None:
    profile = _profile(
        sede_operativa_address="Via Industria 5, 80023 Caivano",
        sede_operativa_lat=40.95,
        sede_operativa_lng=14.30,
        sede_operativa_city="Caivano",
        sede_operativa_province="NA",
    )

    site = await resolve_operating_site(
        profile=profile,
        legal_name="Stub Spa",
        website_domain="stub.it",
        hq_address=profile.hq_address,
        hq_city=profile.hq_city,
        hq_province=profile.hq_province,
        scan_website=_no_call,         # must NOT be called
        forward_geocode=_no_call,      # must NOT be called
        places_search=_no_call,        # must NOT be called
    )

    assert site.source == "atoka"
    assert site.confidence == HIGH_CONFIDENCE
    assert site.lat == 40.95
    assert site.lng == 14.30
    assert site.city == "Caivano"


# ---------------------------------------------------------------------------
# Tier 2 — Website scrape + Mapbox forward_geocode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier2_website_scrape_then_geocode() -> None:
    profile = _profile()  # no sede_operativa

    async def scan(domain, *, http_client=None):
        assert domain == "stub.it"
        return _StubScraped(
            address="Via Test 10",
            cap="20100",
            city="Milano",
            confidence=0.9,
            source_strategy="json_ld",
        )

    async def geocode(query, *, client=None, min_relevance=None):
        assert "Via Test 10" in query
        return _StubGeocode(
            lat=45.46,
            lng=9.18,
            address="Via Test 10, 20100 Milano",
            cap="20100",
            comune="Milano",
            provincia="MI",
            relevance=0.9,
        )

    site = await resolve_operating_site(
        profile=profile,
        legal_name="Stub Spa",
        website_domain="stub.it",
        hq_address=profile.hq_address,
        hq_city=profile.hq_city,
        hq_province=profile.hq_province,
        scan_website=scan,
        forward_geocode=geocode,
        places_search=_no_call,
    )

    assert site.source == "website_scrape"
    assert site.confidence == MEDIUM_CONFIDENCE
    assert site.lat == 45.46
    assert site.city == "Milano"


@pytest.mark.asyncio
async def test_tier2_website_scrape_with_low_relevance_falls_through() -> None:
    """Mapbox returning ``None`` (low relevance) should NOT block tier 3/4."""
    profile = _profile()

    async def scan(domain, *, http_client=None):
        return _StubScraped(address="Via Roma", confidence=0.6)

    async def geocode(query, *, client=None, min_relevance=None):
        # Tier 2 geocode rejects → tier 4 geocode accepts.
        if "Via Roma" in query and "00100" not in query:
            return None
        return _StubGeocode(lat=41.9, lng=12.5, address=query, relevance=0.95)

    async def places(_query, *, client=None):
        return None  # tier 3 also empty

    site = await resolve_operating_site(
        profile=profile,
        legal_name="Stub Spa",
        website_domain="stub.it",
        hq_address=profile.hq_address,
        hq_city=profile.hq_city,
        hq_province=profile.hq_province,
        scan_website=scan,
        forward_geocode=geocode,
        places_search=places,
    )

    assert site.source == "mapbox_hq"
    assert site.confidence == LOW_CONFIDENCE


# ---------------------------------------------------------------------------
# Tier 3 — Google Places
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier3_google_places_when_no_website() -> None:
    profile = _profile()
    cost_meter: dict[str, int] = {}

    async def places(query, *, client=None):
        assert "Stub Spa" in query
        assert "Roma" in query
        return _StubPlace(
            place_id="ChIJxxx",
            formatted_address="Via Places 7, 00100 Roma",
            lat=41.91,
            lng=12.51,
        )

    site = await resolve_operating_site(
        profile=profile,
        legal_name="Stub Spa",
        website_domain=None,
        hq_address=profile.hq_address,
        hq_city=profile.hq_city,
        hq_province=profile.hq_province,
        cost_meter=cost_meter,
        scan_website=_no_call,
        forward_geocode=_no_call,
        places_search=places,
    )

    assert site.source == "google_places"
    assert site.confidence == MEDIUM_CONFIDENCE
    assert site.lat == 41.91
    # Cost meter recorded (2 cents per call per PLACES_COST_PER_CALL_CENTS).
    assert cost_meter.get("google_places") == 2


# ---------------------------------------------------------------------------
# Tier 4 — Mapbox HQ centroid (status quo fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier4_mapbox_hq_centroid_last_resort() -> None:
    profile = _profile()

    async def geocode(query, *, client=None, min_relevance=None):
        assert query == profile.hq_address
        return _StubGeocode(
            lat=profile.hq_lat,
            lng=profile.hq_lng,
            address=query,
            cap="00100",
            comune="Roma",
            provincia="RM",
            relevance=0.95,
        )

    async def places(_query, *, client=None):
        return None

    site = await resolve_operating_site(
        profile=profile,
        legal_name="Stub Spa",
        website_domain=None,
        hq_address=profile.hq_address,
        hq_city=profile.hq_city,
        hq_province=profile.hq_province,
        scan_website=_no_call,
        forward_geocode=geocode,
        places_search=places,
    )

    assert site.source == "mapbox_hq"
    assert site.confidence == LOW_CONFIDENCE
    assert site.lat == profile.hq_lat


# ---------------------------------------------------------------------------
# All sources exhausted → unresolved sentinel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_tiers_fail_returns_empty() -> None:
    async def scan(*_a, **_kw):
        return None

    async def geocode(*_a, **_kw):
        return None

    async def places(*_a, **_kw):
        return None

    site = await resolve_operating_site(
        profile=None,
        legal_name="Unknown Srl",
        website_domain=None,
        hq_address=None,
        hq_city=None,
        hq_province=None,
        scan_website=scan,
        forward_geocode=geocode,
        places_search=places,
    )

    assert site.source == "unresolved"
    assert site.confidence == "none"
    assert site.lat is None and site.lng is None
    assert isinstance(site, OperatingSite)


# ---------------------------------------------------------------------------
# Tier robustness: tier-3 raising an exception must not block tier 4
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_places_exception_falls_through_to_hq() -> None:
    profile = _profile()

    async def places(*_a, **_kw):
        raise RuntimeError("Places API quota exceeded")

    async def geocode(query, *, client=None, min_relevance=None):
        return _StubGeocode(lat=41.9, lng=12.5, address=query, relevance=0.9)

    site = await resolve_operating_site(
        profile=profile,
        legal_name="Stub Spa",
        website_domain=None,
        hq_address=profile.hq_address,
        hq_city=profile.hq_city,
        hq_province=profile.hq_province,
        scan_website=_no_call,
        forward_geocode=geocode,
        places_search=places,
    )

    # Tier 3 raised → caught and logged; tier 4 produced the answer.
    assert site.source == "mapbox_hq"
