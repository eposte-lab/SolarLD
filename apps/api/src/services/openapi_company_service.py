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


def select_render_site(enr: CompanyEnrichment, *, target_provinces: frozenset[str]) -> RenderSite:
    """Fase 4 — pick the PRODUCTIVE site for the render + a confidence.

    Prefers a plant IN the installer's service area (``target_provinces``): a
    big energivoro often has warehouses/plants in several regions, and rendering
    a Parma warehouse for a Campania campaign is worse than useless. So we pick,
    in order: an in-region local unit → the in-region registered office →
    (flagged ``low``) an out-of-region plant → (flagged ``low``) a
    non-productive address. Low confidence → the existing creative gate skips
    the render + queues manual review (a wrong roof destroys the personalisation).
    """

    def in_region(o: CompanyOffice) -> bool:
        return o.province is not None and o.province.upper() in target_provinces

    units = [o for o in enr.offices if o.is_local_unit and o.address_line]
    registered = next((o for o in enr.offices if not o.is_local_unit and o.address_line), None)
    units_region = [o for o in units if in_region(o)]
    reg_region = registered if (registered and in_region(registered)) else None

    if enr.is_productive and units_region:
        u = units_region[0]
        return RenderSite(u.address_line, u.province, "high", "productive_local_unit_in_region")
    if enr.is_productive and reg_region:
        return RenderSite(
            reg_region.address_line, reg_region.province, "high", "productive_registered_in_region"
        )
    # Productive but the plant sits OUTSIDE the service area, or non-productive
    # (office/holding), or no usable address → flag for manual review.
    if enr.is_productive and (units or registered):
        s = units[0] if units else registered
        return RenderSite(s.address_line, s.province, "low", "productive_out_of_region")
    fallback = registered or (units[0] if units else None)
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


# ---------------------------------------------------------------------------
# Fase 1 — cheap geo lookup (IT-start) for the province cost-guard
# ---------------------------------------------------------------------------

# Campania provinces — the original energivori target (kept as a default for
# callers/tests). The geo pass runs on ALL VATs (cheap IT-start) and keeps only
# the target provinces before the expensive IT-marketing enrichment. Filter on
# the REGISTERED-office province (Fase 3 later refines to the actual plant).
TARGET_PROVINCES = frozenset({"NA", "CE", "AV", "BN", "SA"})

# ISO 3166-2:IT province codes per region — the Centro-Sud + Isole service area
# (Delta 2, Change A). Keys are the Italian region names (matched case-folded).
REGION_PROVINCES: dict[str, frozenset[str]] = {
    "Lazio": frozenset({"FR", "LT", "RI", "RM", "VT"}),
    "Abruzzo": frozenset({"AQ", "CH", "PE", "TE"}),
    "Molise": frozenset({"CB", "IS"}),
    "Campania": frozenset({"AV", "BN", "CE", "NA", "SA"}),
    "Puglia": frozenset({"BA", "BT", "BR", "FG", "LE", "TA"}),
    "Basilicata": frozenset({"MT", "PZ"}),
    "Calabria": frozenset({"CS", "CZ", "KR", "RC", "VV"}),
    "Sicilia": frozenset({"AG", "CL", "CT", "EN", "ME", "PA", "RG", "SR", "TP"}),
    "Sardegna": frozenset({"CA", "NU", "OR", "SS", "SU"}),
}
_REGION_BY_FOLD = {k.casefold(): k for k in REGION_PROVINCES}


def provinces_for_regions(
    regions: list[str] | tuple[str, ...], *, include_roma: bool = True
) -> frozenset[str]:
    """Expand region names → the set of their province codes (Change A).

    Unknown region names are ignored (logged by the caller if needed). With
    ``include_roma=False`` the RM province is dropped (Rome is a special, huge,
    often-out-of-area market the operator may want to exclude)."""
    out: set[str] = set()
    for r in regions or ():
        canon = _REGION_BY_FOLD.get((r or "").strip().casefold())
        if canon:
            out |= REGION_PROVINCES[canon]
    if not include_roma:
        out.discard("RM")
    return frozenset(out)


@dataclass(frozen=True)
class CompanyGeo:
    piva: str
    company_name: str | None = None
    province: str | None = None  # 2-letter registered-office province
    town: str | None = None
    lat: float | None = None
    lng: float | None = None
    activity_status: str | None = None


def parse_it_start(payload: Any, piva: str) -> CompanyGeo | None:
    """Map an IT-start response → geo (PURE). NB IT-start ``data`` is a LIST and
    ``registeredOffice.province`` is a plain 2-letter string (not a dict)."""
    rec = _first_record(payload)
    if rec is None:
        return None
    ro = (rec.get("address") or {}).get("registeredOffice") or {}
    prov = ro.get("province")
    prov_code = prov.get("code") if isinstance(prov, dict) else prov
    gps = (ro.get("gps") or {}).get("coordinates") or []
    lng = float(gps[0]) if len(gps) >= 2 else None  # GeoJSON order: [lng, lat]
    lat = float(gps[1]) if len(gps) >= 2 else None
    prov_norm = (_s(prov_code) or "").upper() or None
    return CompanyGeo(
        piva=piva,
        company_name=_s(rec.get("companyName")),
        province=prov_norm,
        town=_s(ro.get("town")),
        lat=lat,
        lng=lng,
        activity_status=_s(rec.get("activityStatus")),
    )


def is_target_province(province: str | None, targets: frozenset[str] = TARGET_PROVINCES) -> bool:
    return bool(province) and province.upper() in targets


async def fetch_company_geo(
    piva: str, *, client: httpx.AsyncClient | None = None
) -> CompanyGeo | None:
    """Fase 1 — cheap geo lookup by P.IVA (province + coords) for the filter."""
    return parse_it_start(await _get(f"/IT-start/{piva}", client=client), piva)


# ---------------------------------------------------------------------------
# IT-stakeholders — the Registro Imprese decision-maker ("persona responsabile")
# ---------------------------------------------------------------------------
#
# GET /IT-stakeholders/{piva} → data with a ``managers`` list; each manager:
#   {isLegalRepresentative: bool, name, surname, taxCode,
#    roles: [{role: {code, description}, roleStartDate}], gender, age, ...}
# Confirmed live: role code AUN='Managing director' (amministratore unico),
# PC='Procurator', SIE/PCS = auditors (sindaci) which are NOT decision-makers.
# The legale rappresentante / amministratore is the buyer in the small family
# SRLs that make up the energivori list — the registry has this name ~100% of
# the time, so it becomes the PRIMARY anchor (LinkedIn/Hunter = confirmation).

# Governing-role priority (higher = more decisional). Keyed on the OpenAPI role
# code; unknown codes fall back to the description keywords below.
_ROLE_PRIORITY: dict[str, int] = {
    "AUN": 100,  # amministratore unico (Managing director)
    "AD": 90,  # amministratore delegato
    "PCC": 80,  # presidente CdA
    "PRE": 80,  # presidente
    "AMM": 70,  # amministratore / consigliere
    "CO": 65,  # consigliere
    "PC": 40,  # procuratore (has signing power, not a governing role)
}
_ROLE_LABEL_IT: dict[str, str] = {
    "AUN": "Amministratore unico",
    "AD": "Amministratore delegato",
    "PCC": "Presidente CdA",
    "PRE": "Presidente",
    "AMM": "Amministratore",
    "CO": "Consigliere",
    "PC": "Procuratore",
}
# Control/auditor roles — NEVER the decision-maker (collegio sindacale, revisori).
_AUDITOR_CODES = frozenset({"SIE", "SIS", "PCS", "REV", "RE", "PRS"})
_AUDITOR_KEYWORDS = ("auditor", "sindac", "revisor", "collegio")


@dataclass(frozen=True)
class RegistroManager:
    name: str | None
    surname: str | None
    tax_code: str | None
    is_legal_rep: bool
    roles: tuple[tuple[str | None, str | None], ...]  # (code, description) pairs


@dataclass(frozen=True)
class RegistroDecisionMaker:
    full_name: str  # "Dante Mele" (title-cased)
    first_name: str | None
    last_name: str | None
    role: str  # Italian label, e.g. "Amministratore unico"
    role_code: str | None
    confidence: str  # 'alta' | 'media'
    is_legal_rep: bool


def _is_auditor(code: str | None, desc: str | None) -> bool:
    if code and code.upper() in _AUDITOR_CODES:
        return True
    d = (desc or "").lower()
    return any(k in d for k in _AUDITOR_KEYWORDS)


def _role_score(code: str | None, desc: str | None) -> int:
    if code and code.upper() in _ROLE_PRIORITY:
        return _ROLE_PRIORITY[code.upper()]
    d = (desc or "").lower()
    if "managing director" in d:
        return 100
    if "chief executive" in d or d == "ceo":
        return 90
    if "chairman" in d or "president" in d:  # auditors already filtered out
        return 80
    if "director" in d or "administrator" in d or "amministrat" in d:
        return 70
    if "procurator" in d or "attorney" in d or "representative" in d:
        return 40
    return 50  # unknown governing role


def _role_label(code: str | None, desc: str | None) -> str:
    if code and code.upper() in _ROLE_LABEL_IT:
        return _ROLE_LABEL_IT[code.upper()]
    return (desc or "").strip() or "Rappresentante"


def _person_name(name: str | None, surname: str | None) -> str:
    parts = [p.strip().title() for p in (name, surname) if p and p.strip()]
    return " ".join(parts)


def parse_it_stakeholders(payload: Any, piva: str) -> list[RegistroManager]:
    """Map an IT-stakeholders response → the managers (PURE). Empty on missing."""
    rec = _first_record(payload)
    if rec is None:
        return []
    out: list[RegistroManager] = []
    for m in rec.get("managers") or []:
        if not isinstance(m, dict):
            continue
        roles: list[tuple[str | None, str | None]] = []
        for r in m.get("roles") or []:
            role = (r or {}).get("role") or {}
            roles.append((_s(role.get("code")), _s(role.get("description"))))
        out.append(
            RegistroManager(
                name=_s(m.get("name")),
                surname=_s(m.get("surname")),
                tax_code=_s(m.get("taxCode")),
                is_legal_rep=bool(m.get("isLegalRepresentative")),
                roles=tuple(roles),
            )
        )
    return out


def resolve_registro_decision_maker(
    managers: list[RegistroManager],
) -> RegistroDecisionMaker | None:
    """Fase 1 (Modifica 1) — pick the decision-maker from the registry (PURE).

    Excludes auditors (collegio sindacale/revisori); ranks the rest by governing
    role (amministratore unico > delegato > presidente CdA > … > procuratore),
    boosting the flagged legale rappresentante. Returns None when the registry
    has only auditors / no usable person.
    """
    best: RegistroManager | None = None
    best_key: tuple[int, int] | None = None
    best_role: tuple[str | None, str | None] = (None, None)
    for m in managers:
        if not (m.name or m.surname):
            continue
        # the manager's best NON-auditor role
        gov = [(c, d) for (c, d) in m.roles if not _is_auditor(c, d)]
        if not gov and m.roles:
            continue  # every role is an auditor role → not a decision-maker
        code, desc = max(gov, key=lambda cd: _role_score(*cd)) if gov else (None, None)
        score = _role_score(code, desc) + (25 if m.is_legal_rep else 0)
        key = (score, 1 if m.is_legal_rep else 0)
        if best_key is None or key > best_key:
            best, best_key, best_role = m, key, (code, desc)
    if best is None:
        return None
    code, desc = best_role
    confidence = "alta" if (best.is_legal_rep or _role_score(code, desc) >= 90) else "media"
    return RegistroDecisionMaker(
        full_name=_person_name(best.name, best.surname),
        first_name=(best.name or "").strip().title() or None,
        last_name=(best.surname or "").strip().title() or None,
        role=_role_label(code, desc),
        role_code=code,
        confidence=confidence,
        is_legal_rep=best.is_legal_rep,
    )


async def fetch_company_stakeholders(
    piva: str, *, client: httpx.AsyncClient | None = None
) -> list[RegistroManager]:
    """Fetch the Registro Imprese managers by P.IVA (IT-stakeholders)."""
    return parse_it_stakeholders(await _get(f"/IT-stakeholders/{piva}", client=client), piva)
