"""Italian business-data enrichment.

Two providers, used in sequence:

  1. **Visura.it** — official cadastral lookup: given a lat/lng (or a
     parcel ID), returns the "intestatario" of the building. For private
     persons returns name + tax code; for companies returns P.IVA + legal
     name.

  2. **Atoka (SpazioDati)** — P.IVA → full company profile:
     `legal_name`, `ateco_code`, `revenue`, `employees`, `decision_makers`,
     `website_domain`.

Both APIs currently have key placeholders (user will supply them later).
This module's `VisuraClient` / `AtokaClient` are production-ready HTTP
wrappers — when the key is missing they raise `EnrichmentUnavailable`
which the Identity agent catches to fall through to partial enrichment.

Doc links (for when keys arrive):
  - https://www.visura.it/sviluppatori/api-catasto
  - https://atoka.io/public/api/v2/doc
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger
from ..models.enums import SubjectType

log = get_logger(__name__)


class EnrichmentUnavailable(Exception):
    """Raised when the provider is not configured or returned no data."""


# ---------------------------------------------------------------------------
# Visura — cadastral lookup
# ---------------------------------------------------------------------------

VISURA_BASE = "https://api.visura.it/v1"
VISURA_COST_PER_CALL_CENTS = 25  # €0.25 per official cadastral query


@dataclass(slots=True)
class VisuraOwner:
    classification: SubjectType
    # B2B
    business_name: str | None = None
    vat_number: str | None = None
    # B2C
    owner_first_name: str | None = None
    owner_last_name: str | None = None
    fiscal_code: str | None = None
    # Shared
    postal_address: str | None = None
    postal_cap: str | None = None
    postal_city: str | None = None
    postal_province: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
async def visura_lookup_by_coords(
    lat: float,
    lng: float,
    *,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> VisuraOwner:
    """Resolve a roof centroid into its cadastral owner."""
    key = api_key or settings.visura_api_key
    if not key:
        raise EnrichmentUnavailable("VISURA_API_KEY not configured")

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=20.0)
    try:
        resp = await client.post(
            f"{VISURA_BASE}/catasto/lookup",
            headers={"Authorization": f"Bearer {key}"},
            json={"lat": lat, "lng": lng},
        )
    finally:
        if owns_client:
            await client.aclose()

    if resp.status_code == 404:
        raise EnrichmentUnavailable("visura_no_match")
    if resp.status_code >= 400:
        raise EnrichmentUnavailable(f"visura_http_{resp.status_code}")

    data = resp.json()
    owner = (data.get("intestatario") or {})
    is_company = bool(owner.get("partita_iva"))

    return VisuraOwner(
        classification=SubjectType.B2B if is_company else SubjectType.B2C,
        business_name=owner.get("ragione_sociale"),
        vat_number=owner.get("partita_iva"),
        owner_first_name=owner.get("nome"),
        owner_last_name=owner.get("cognome"),
        fiscal_code=owner.get("codice_fiscale"),
        postal_address=owner.get("indirizzo"),
        postal_cap=owner.get("cap"),
        postal_city=owner.get("comune"),
        postal_province=owner.get("provincia"),
        raw=data,
    )


# ---------------------------------------------------------------------------
# Atoka — company profile by P.IVA
# ---------------------------------------------------------------------------

ATOKA_BASE = "https://api.atoka.io/v2"
ATOKA_COST_PER_CALL_CENTS = 15  # €0.15 per company lookup on Business plan
# Discovery (atoka_search_by_criteria) is billed per company returned rather
# than per call. The SpazioDati Business contract quotes €3 per 1000 company
# records in search responses — 0.3 cents/record, rounded up for safety.
ATOKA_DISCOVERY_COST_PER_RECORD_CENTS = 1


@dataclass(slots=True)
class AtokaProfile:
    vat_number: str
    legal_name: str
    ateco_code: str | None
    ateco_description: str | None
    yearly_revenue_cents: int | None
    employees: int | None
    website_domain: str | None
    decision_maker_name: str | None
    decision_maker_role: str | None
    linkedin_url: str | None
    # Phone number is part of the `includeContacts:true` bundle we
    # already pay for (€0.15/lookup). Atoka returns it under several
    # possible keys (`raw.phones[]`, `raw.contacts[].value`,
    # `raw.base.phone`); see `_extract_phone()` for the merge logic.
    # NULL = Atoka had no phone for this VAT (≈30% of B2B records).
    phone: str | None = None
    # HQ address — populated by discovery search, left empty by single-lookup
    # for backwards compat. HunterAgent's ATECO-precision pipeline relies on
    # these to forward-geocode → Solar.
    hq_address: str | None = None
    hq_cap: str | None = None
    hq_city: str | None = None
    hq_province: str | None = None
    hq_lat: float | None = None
    hq_lng: float | None = None
    # Sede operativa (operating site) — distinct from the legal HQ when
    # the business runs out of a warehouse, factory or branch that's not
    # the registered office. Critical for the rooftop render: legal HQs
    # are often a notary's address or a centroid of an industrial zone,
    # while the operating site is the actual building we want to paint
    # solar panels onto. Populated from `locations[]` entries whose
    # `type` is one of {operating, secondary, production, branch} —
    # parser falls back to None when only the registered HQ exists.
    sede_operativa_address: str | None = None
    sede_operativa_cap: str | None = None
    sede_operativa_city: str | None = None
    sede_operativa_province: str | None = None
    sede_operativa_lat: float | None = None
    sede_operativa_lng: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
async def atoka_lookup_by_vat(
    vat_number: str,
    *,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> AtokaProfile:
    """Resolve a P.IVA into a full Atoka company profile."""
    key = api_key or settings.atoka_api_key
    if not key:
        raise EnrichmentUnavailable("ATOKA_API_KEY not configured")

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=20.0)
    try:
        resp = await client.get(
            f"{ATOKA_BASE}/companies",
            headers={"Authorization": f"Token {key}"},
            params={"vat": vat_number, "includeContacts": "true"},
        )
    finally:
        if owns_client:
            await client.aclose()

    if resp.status_code == 404:
        raise EnrichmentUnavailable("atoka_no_match")
    if resp.status_code >= 400:
        raise EnrichmentUnavailable(f"atoka_http_{resp.status_code}")

    body = resp.json()
    items = body.get("items") or []
    if not items:
        raise EnrichmentUnavailable("atoka_empty")
    company = items[0]

    financials = company.get("financials") or {}
    revenue_eur = financials.get("revenue")
    ateco = (company.get("ateco") or [{}])[0] if company.get("ateco") else {}
    contacts = company.get("decisionMakers") or []
    primary_contact = contacts[0] if contacts else {}
    web = (company.get("web") or [{}])[0] if company.get("web") else {}

    return _atoka_company_to_profile(company, fallback_vat=vat_number)


# ---------------------------------------------------------------------------
# Atoka — multi-criteria discovery (ATECO + geo + firmographics)
# ---------------------------------------------------------------------------
#
# Used by HunterAgent's `b2b_ateco_precision` mode: instead of Google Places
# Nearby Search, we ask Atoka directly for companies matching the tenant's
# ideal customer profile (ATECO codes, province, size). Each returned profile
# comes with the HQ address, which downstream gets forward-geocoded and fed
# into Google Solar for roof suitability.


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
async def atoka_search_by_criteria(
    *,
    ateco_codes: list[str],
    province_code: str | None = None,
    region_code: str | None = None,
    employees_min: int | None = None,
    employees_max: int | None = None,
    revenue_min_eur: int | None = None,
    revenue_max_eur: int | None = None,
    limit: int = 500,
    offset: int = 0,
    active_only: bool = True,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> list[AtokaProfile]:
    """Discovery: restituisce aziende italiane che matchano i criteri.

    Wrapper su ``GET /companies`` Atoka v2. Paginato (Atoka cap 100 per
    pagina): il chiamante richiede ``limit`` totale e noi facciamo tanti
    hop di pagina quanti servono. Ordine risultati: Atoka default (nessun
    ``sort`` esplicito) che tipicamente è per relevance.

    Costo: ~€0.003 per azienda restituita. Una ricerca da 500 aziende ≈
    €1.50 — pienamente dentro budget discovery di un run Hunter tipico.

    Raises:
        EnrichmentUnavailable: se la key non è configurata o Atoka risponde
            con 4xx non recuperabile. Il chiamante (HunterAgent) degrada a
            modalità ``opportunistic`` o abortisce il run a seconda della
            politica config.
    """
    # ── Mock mode ────────────────────────────────────────────────────────────
    # Active when ATOKA_MOCK_MODE=true.  Generates deterministic synthetic
    # profiles so the full funnel can be exercised without a real key.
    # A real key (if present) always takes priority — mock only fires when
    # neither a per-call `api_key` nor the global `settings.atoka_api_key`
    # is configured.
    effective_key = api_key or settings.atoka_api_key
    if settings.atoka_mock_mode and not effective_key:
        if not ateco_codes:
            raise ValueError("ateco_codes cannot be empty — would return entire Italian market")
        from .atoka_mock import generate_mock_atoka_profiles  # lazy import
        mock_province = province_code or (region_code[:2].upper() if region_code else "NA")
        mock_count = min(limit, settings.atoka_mock_count)
        log.info(
            "atoka_mock_active",
            extra={
                "province": mock_province,
                "ateco_codes": ateco_codes,
                "mock_count": mock_count,
            },
        )
        return generate_mock_atoka_profiles(
            ateco_codes=ateco_codes,
            province_code=mock_province,
            count=mock_count,
        )

    key = effective_key
    if not key:
        raise EnrichmentUnavailable("ATOKA_API_KEY not configured")
    if not ateco_codes:
        raise ValueError("ateco_codes cannot be empty — would return entire Italian market")

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)

    collected: list[AtokaProfile] = []
    # Atoka v2 paginates with `offset` + `limit`. Page size capped at 100.
    page_size = min(100, limit)
    current_offset = offset

    try:
        while len(collected) < limit:
            remaining = limit - len(collected)
            params: dict[str, Any] = {
                # Atoka accepts comma-separated ATECO codes. Supports both
                # 6-char (10.11.00) and 4-char prefix (10.11) semantics.
                "ateco": ",".join(ateco_codes),
                "limit": min(page_size, remaining),
                "offset": current_offset,
                "includeContacts": "true",
            }
            if province_code:
                params["locationAreaProvince"] = province_code
            if region_code:
                params["locationAreaRegion"] = region_code
            if employees_min is not None or employees_max is not None:
                # Atoka range syntax: "min-max". Open ranges use empty side.
                lo = "" if employees_min is None else str(employees_min)
                hi = "" if employees_max is None else str(employees_max)
                params["employeesRange"] = f"{lo}-{hi}"
            if revenue_min_eur is not None or revenue_max_eur is not None:
                lo = "" if revenue_min_eur is None else str(revenue_min_eur)
                hi = "" if revenue_max_eur is None else str(revenue_max_eur)
                params["revenueRange"] = f"{lo}-{hi}"
            if active_only:
                params["active"] = "true"

            resp = await client.get(
                f"{ATOKA_BASE}/companies",
                headers={"Authorization": f"Token {key}"},
                params=params,
            )

            if resp.status_code == 404:
                # No matches — legitimate empty result, not an error.
                break
            if resp.status_code == 401:
                raise EnrichmentUnavailable("atoka_auth_failed")
            if resp.status_code == 429:
                # Let tenacity retry with backoff.
                raise EnrichmentUnavailable("atoka_rate_limited")
            if resp.status_code >= 400:
                raise EnrichmentUnavailable(f"atoka_http_{resp.status_code}")

            body = resp.json()
            items = body.get("items") or []
            if not items:
                break

            for company in items:
                vat = (company.get("vat") or company.get("vatNumber") or "").strip()
                if not vat:
                    # Atoka occasionally returns companies without a public
                    # VAT (e.g. sole proprietorships in probate). Skip —
                    # downstream keyed by P.IVA.
                    continue
                collected.append(_atoka_company_to_profile(company, fallback_vat=vat))

            # If Atoka returned fewer than requested, we've exhausted the
            # result set — stop paginating.
            if len(items) < params["limit"]:
                break
            current_offset += len(items)
    finally:
        if owns_client:
            await client.aclose()

    log.info(
        "atoka_discovery_completed",
        extra={
            "ateco_count": len(ateco_codes),
            "province": province_code,
            "region": region_code,
            "requested": limit,
            "returned": len(collected),
        },
    )
    return collected


def _extract_phone(company: dict[str, Any]) -> str | None:
    """Pull a phone number out of an Atoka company payload.

    Atoka's response shape drifts between plan tiers and endpoints
    (`/companies` vs `/search`), so we probe each known location and
    return the first hit:

      - ``company.phones`` → list of strings or ``{number, value}`` dicts
      - ``company.contacts`` → list of ``{type:"phone", value:"..."}``
      - ``company.base.phone`` → single string fallback

    Returns ``None`` if no phone is present (Atoka has it for ~70% of
    B2B records — partita IVA-only entities and very small SRL often
    don't publish a number).
    """
    if not company:
        return None

    phones = company.get("phones")
    if isinstance(phones, list) and phones:
        first = phones[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
        if isinstance(first, dict):
            v = first.get("number") or first.get("value")
            if v:
                return str(v).strip()

    for c in company.get("contacts") or []:
        if isinstance(c, dict) and c.get("type") == "phone":
            v = c.get("value")
            if v:
                return str(v).strip()

    base = company.get("base") or {}
    if isinstance(base, dict):
        v = base.get("phone")
        if v:
            return str(v).strip()

    return None


def _atoka_company_to_profile(
    company: dict[str, Any], *, fallback_vat: str
) -> AtokaProfile:
    """Parse one Atoka company payload into our normalized AtokaProfile.

    Robust to field naming drift between Atoka's docs and actual responses:
    we've seen `vat` vs `vatNumber`, `employeesCount` vs `employees`,
    `revenues` vs `financials.revenue`. The fallback_vat arg is used when
    the caller already knows the VAT (e.g. single-lookup path).
    """
    financials = company.get("financials") or {}
    revenue_eur = (
        financials.get("revenue")
        or company.get("revenue")
        or company.get("revenues")
    )
    ateco_list = company.get("ateco") or []
    ateco = ateco_list[0] if ateco_list else {}
    contacts = company.get("decisionMakers") or []
    primary_contact = contacts[0] if contacts else {}
    web = (company.get("web") or [{}])[0] if company.get("web") else {}

    # HQ address can be in a few shapes depending on Atoka plan tier.
    locations = company.get("locations") or []
    hq = locations[0] if locations else (company.get("base") or {})
    hq_addr = hq.get("address") or hq.get("street")
    hq_lat = hq.get("lat") or (hq.get("coords") or {}).get("lat")
    hq_lng = hq.get("lng") or (hq.get("coords") or {}).get("lng")

    # Sede operativa: scan `locations[]` for an entry that explicitly
    # describes an operating site. Atoka uses `type` strings like
    # "operating", "secondary", "production", "branch" — we accept any
    # of these and prefer the first match (Atoka orders them by
    # significance). When none of the locations are tagged we leave
    # the sede_operativa_* fields null and let the resolver fall
    # through to website-scrape / Google Places.
    OPERATING_TYPES = {
        "operating",
        "secondary",
        "production",
        "branch",
        "operativa",
        "secondaria",
    }
    op_loc: dict[str, Any] | None = None
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        loc_type = (loc.get("type") or loc.get("kind") or "").lower()
        if loc_type in OPERATING_TYPES:
            op_loc = loc
            break
    op_addr = op_lat = op_lng = None
    op_cap = op_city = op_prov = None
    if op_loc:
        op_addr = op_loc.get("address") or op_loc.get("street")
        op_cap = op_loc.get("zip") or op_loc.get("cap")
        op_city = op_loc.get("city") or op_loc.get("comune")
        op_prov = op_loc.get("province") or op_loc.get("provincia")
        op_lat = op_loc.get("lat") or (op_loc.get("coords") or {}).get("lat")
        op_lng = op_loc.get("lng") or (op_loc.get("coords") or {}).get("lng")

    return AtokaProfile(
        vat_number=company.get("vat") or company.get("vatNumber") or fallback_vat,
        legal_name=company.get("name") or company.get("legalName") or "",
        ateco_code=ateco.get("code"),
        ateco_description=ateco.get("description"),
        yearly_revenue_cents=int(revenue_eur * 100) if revenue_eur else None,
        employees=company.get("employees") or company.get("employeesCount"),
        website_domain=(web.get("url") or "")
        .replace("https://", "")
        .replace("http://", "")
        .strip("/"),
        decision_maker_name=primary_contact.get("name"),
        decision_maker_role=primary_contact.get("role"),
        linkedin_url=primary_contact.get("linkedin"),
        phone=_extract_phone(company),
        hq_address=hq_addr,
        hq_cap=hq.get("zip") or hq.get("cap"),
        hq_city=hq.get("city") or hq.get("comune"),
        hq_province=hq.get("province") or hq.get("provincia"),
        hq_lat=float(hq_lat) if hq_lat is not None else None,
        hq_lng=float(hq_lng) if hq_lng is not None else None,
        sede_operativa_address=op_addr,
        sede_operativa_cap=op_cap,
        sede_operativa_city=op_city,
        sede_operativa_province=op_prov,
        sede_operativa_lat=float(op_lat) if op_lat is not None else None,
        sede_operativa_lng=float(op_lng) if op_lng is not None else None,
        raw=company,
    )
