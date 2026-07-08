"""OpenAPI.it Company product — enrich an Italian company by P.IVA.

The energivori channel starts from a list of VATs, so we look up each company
BY P.IVA (unlike the existing ``italian_company_lookup`` which SEARCHES by
ATECO on ``imprese.openapi.it/advance``). The Company product lives on a
DIFFERENT host — ``https://company.openapi.com`` (``.com``, not ``.it``) — with
direct GET endpoints, Bearer auth (the shared ``openapi_it_token``):

  * ``/IT-start/{piva}``     — cheap: name, registered office + GPS, status,
    province. Used for Fase 1 geo (on ALL VATs → filter to target provinces)
    BEFORE the expensive enrichment. NB ``data`` is a LIST here.
  * ``/IT-marketing/{piva}`` — rich: contacts (phone), pec, mail, website,
    ateco (with a manufacturing macro-class), employees, and ``allOffices[]``
    (registered office ``SSL`` vs local unit ``UL``). Used for Fase 3/4 on the
    filtered subset. NB ``data`` is a DICT here.

Field names below are the REAL ones confirmed against the live API (camelCase),
not the snake_case the original spec assumed. Parsing is defensive + PURE so it
is unit-tested against captured fixtures; the async fetchers are thin wrappers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

COMPANY_BASE_URL = "https://company.openapi.com"
_TIMEOUT = httpx.Timeout(connect=5.0, read=45.0, write=10.0, pool=10.0)

# ATECO macro-class codes that denote a real production/manufacturing site (a
# roof worth rendering): C = manufacturing, B = mining, D = energy, F = building
# sites. Everything else (G retail, K finance, L real-estate, M studios…) is an
# office, not a plant.
_PRODUCTIVE_MACRO = {"C", "B", "D", "F"}


@dataclass(frozen=True)
class CompanyOffice:
    office_type: str  # raw code, e.g. 'SSL' (registered) | 'UL' (local unit)
    is_local_unit: bool
    street: str | None
    town: str | None
    province: str | None  # 2-letter code
    zip_code: str | None

    @property
    def address_line(self) -> str | None:
        parts = [p for p in (self.street, self.zip_code, self.town, self.province) if p]
        return ", ".join(parts) if parts else None


@dataclass(frozen=True)
class CompanyEnrichment:
    piva: str
    company_name: str | None = None
    province: str | None = None  # registered-office province code (for Fase 2)
    town: str | None = None
    phone: str | None = None
    email: str | None = None
    pec: str | None = None
    website: str | None = None
    ateco_code: str | None = None
    ateco_description: str | None = None
    ateco_macro: str | None = None  # firstLevel code, e.g. 'C'
    employees: int | None = None
    activity_status: str | None = None
    offices: list[CompanyOffice] = field(default_factory=list)

    @property
    def is_productive(self) -> bool:
        return (self.ateco_macro or "").upper() in _PRODUCTIVE_MACRO


def _first_record(payload: Any) -> dict[str, Any] | None:
    """``data`` is a LIST on IT-start and a DICT on IT-marketing — normalise."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, list):
        return data[0] if data and isinstance(data[0], dict) else None
    return data if isinstance(data, dict) else None


def _s(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _parse_office(o: dict[str, Any]) -> CompanyOffice | None:
    cd = o.get("companyDetails") or {}
    ot = (cd.get("officeType") or {}).get("code")
    addr = o.get("address") or {}
    prov = addr.get("province")
    prov_code = prov.get("code") if isinstance(prov, dict) else prov
    return CompanyOffice(
        office_type=str(ot or "").upper(),
        is_local_unit=str(ot or "").upper() == "UL",
        street=_s(addr.get("streetName")),
        town=_s(addr.get("town")),
        province=_s(prov_code),
        zip_code=_s(addr.get("zipCode")),
    )


def parse_it_marketing(payload: Any, piva: str) -> CompanyEnrichment | None:
    """Map an IT-marketing response → normalised enrichment (PURE)."""
    rec = _first_record(payload)
    if rec is None:
        return None

    contacts = rec.get("contacts") or {}
    ateco_cls = rec.get("atecoClassification") or {}
    ateco = ateco_cls.get("ateco") or {}
    macro = ((ateco_cls.get("firstLevel") or {}).get("ateco") or {}).get("code")
    emp = rec.get("employees") or {}
    web = rec.get("webAndSocial") or {}
    mail = rec.get("mail") or {}
    cd = rec.get("companyDetails") or {}

    offices = [
        off
        for o in (rec.get("allOffices") or [])
        if isinstance(o, dict) and (off := _parse_office(o)) is not None
    ]
    # Registered-office province drives the Fase-2 geo filter.
    reg_prov = next((o.province for o in offices if not o.is_local_unit), None)
    reg_town = next((o.town for o in offices if not o.is_local_unit), None)

    return CompanyEnrichment(
        piva=piva,
        company_name=_s(cd.get("companyName")),
        province=reg_prov or (offices[0].province if offices else None),
        town=reg_town or (offices[0].town if offices else None),
        phone=_s(contacts.get("telephoneNumber")),
        email=_s(mail.get("email")),
        pec=_s(rec.get("pec")),
        website=_s(web.get("website")),
        ateco_code=_s(ateco.get("code")),
        ateco_description=_s(ateco.get("description")),
        ateco_macro=_s(macro),
        employees=emp.get("employee") if isinstance(emp.get("employee"), int) else None,
        activity_status=_s((rec.get("companyStatus") or {}).get("activityStatus")),
        offices=offices,
    )


@dataclass(frozen=True)
class RenderSite:
    """The address to point the render at, with a confidence for the gate."""

    address_line: str | None
    province: str | None
    confidence: str  # 'high' | 'low'
    reason: str


def select_render_site(enr: CompanyEnrichment) -> RenderSite:
    """Fase 4 — pick the PRODUCTIVE site for the render + a confidence.

    A wrong roof is worse than no roof (it destroys the personalisation), so a
    non-productive/ambiguous company yields ``low`` confidence → the existing
    creative gate skips the render and queues manual review.
    """
    local_units = [o for o in enr.offices if o.is_local_unit and o.address_line]
    registered = next((o for o in enr.offices if not o.is_local_unit and o.address_line), None)

    if enr.is_productive and local_units:
        u = local_units[0]
        return RenderSite(u.address_line, u.province, "high", "productive_local_unit")
    if enr.is_productive and registered:
        return RenderSite(registered.address_line, registered.province, "high", "productive_registered")
    # Non-productive (office/holding) or no usable address → flag it.
    fallback = registered or (local_units[0] if local_units else None)
    return RenderSite(
        fallback.address_line if fallback else None,
        fallback.province if fallback else enr.province,
        "low",
        "non_productive_ateco" if not enr.is_productive else "no_address",
    )


async def _get(path: str, *, client: httpx.AsyncClient | None) -> Any | None:
    token = (settings.openapi_it_token or "").strip()
    if not token:
        log.info("openapi_company.skip_no_token")
        return None
    owns = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        resp = await client.get(
            f"{COMPANY_BASE_URL}{path}", headers={"Authorization": f"Bearer {token}"}
        )
        if resp.status_code >= 400:
            log.warning("openapi_company.bad_response", path=path, status=resp.status_code)
            return None
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("openapi_company.http_error", path=path, err=type(exc).__name__)
        return None
    finally:
        if owns:
            await client.aclose()


async def fetch_company_enrichment(
    piva: str, *, client: httpx.AsyncClient | None = None
) -> CompanyEnrichment | None:
    """Fase 3 — full enrichment by P.IVA (contacts, pec, ateco, offices)."""
    return parse_it_marketing(await _get(f"/IT-marketing/{piva}", client=client), piva)
