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

    return AtokaProfile(
        vat_number=vat_number,
        legal_name=company.get("name", ""),
        ateco_code=ateco.get("code"),
        ateco_description=ateco.get("description"),
        yearly_revenue_cents=int(revenue_eur * 100) if revenue_eur else None,
        employees=company.get("employees"),
        website_domain=(web.get("url") or "").replace("https://", "").replace("http://", "").strip("/"),
        decision_maker_name=primary_contact.get("name"),
        decision_maker_role=primary_contact.get("role"),
        linkedin_url=primary_contact.get("linkedin"),
        raw=company,
    )
