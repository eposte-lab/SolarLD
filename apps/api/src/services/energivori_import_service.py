"""Energivori channel — orchestrate a VAT record into an enriched prospect.

Chains the confirmed OpenAPI building blocks for one company:
  Fase 1  geo (IT-start, cheap)     → keep only target provinces
  Fase 3  enrich (IT-marketing)     → contacts, pec, ateco, local units
  Fase 4  select the productive site + a render confidence

The result is a flat ``EnrichedProspect`` ready to become a ``prospect_list_item``
(the existing validate → render → send backbone takes it from there). Kept
free of DB writes so a ``dry_run`` can price a batch before spending: the geo
pass is the only cost on non-target companies; the expensive enrichment runs
only on the filtered subset.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..core.logging import get_logger
from .energivori_ingest import EnergivoroRecord
from .openapi_company_service import (
    _TIMEOUT,
    TARGET_PROVINCES,
    CompanyEnrichment,
    RenderSite,
    fetch_company_enrichment,
    fetch_company_geo,
    is_target_province,
    select_render_site,
)

log = get_logger(__name__)

# OpenAPI.it pay-as-you-go unit costs (cents) — for the dry-run estimate.
_COST_GEO_CENTS = 5     # IT-start (cheap, on ALL vats)
_COST_ENRICH_CENTS = 10  # IT-marketing (only the filtered subset)


@dataclass(frozen=True)
class EnrichedProspect:
    piva: str
    ragione_sociale: str
    province: str | None
    town: str | None
    settore_csea: str | None  # from the CSEA list (bonus signal)
    # enrichment
    phone: str | None = None
    email: str | None = None
    pec: str | None = None
    website: str | None = None
    ateco_code: str | None = None
    employees: int | None = None
    # render target (Fase 4)
    render_address: str | None = None
    render_province: str | None = None
    render_confidence: str = "low"
    render_reason: str = "not_enriched"


@dataclass
class ImportSummary:
    total: int = 0
    geo_ok: int = 0
    in_target: int = 0
    enriched: int = 0
    render_high: int = 0
    with_email: int = 0
    est_cost_cents: int = 0
    prospects: list[EnrichedProspect] | None = None


def _to_prospect(
    rec: EnergivoroRecord, province: str | None, town: str | None,
    enr: CompanyEnrichment | None, site: RenderSite | None,
) -> EnrichedProspect:
    return EnrichedProspect(
        piva=rec.piva,
        ragione_sociale=rec.ragione_sociale,
        province=province,
        town=town,
        settore_csea=rec.settore,
        phone=enr.phone if enr else None,
        email=enr.email if enr else None,
        pec=enr.pec if enr else None,
        website=enr.website if enr else None,
        ateco_code=enr.ateco_code if enr else None,
        employees=enr.employees if enr else None,
        render_address=site.address_line if site else None,
        render_province=site.province if site else province,
        render_confidence=site.confidence if site else "low",
        render_reason=site.reason if site else "not_enriched",
    )


async def enrich_record(
    rec: EnergivoroRecord, *, client: httpx.AsyncClient
) -> tuple[EnrichedProspect | None, int]:
    """Enrich one VAT. Returns (prospect | None if out-of-target, cost_cents).

    ``None`` prospect means the cheap geo pass filtered it out (not in a target
    province) — only the geo cost was spent.
    """
    geo = await fetch_company_geo(rec.piva, client=client)
    cost = _COST_GEO_CENTS
    if geo is None:
        return None, cost
    if not is_target_province(geo.province):
        return None, cost  # filtered out — no enrichment spend

    enr = await fetch_company_enrichment(rec.piva, client=client)
    cost += _COST_ENRICH_CENTS
    site = select_render_site(enr, target_provinces=TARGET_PROVINCES) if enr else None
    return _to_prospect(rec, geo.province, geo.town, enr, site), cost


async def run_import(
    records: list[EnergivoroRecord], *, limit: int | None = None
) -> ImportSummary:
    """Enrich a batch (Fasi 1-4). No DB writes — safe to dry-run + price."""
    batch = records[:limit] if limit else records
    s = ImportSummary(total=len(batch), prospects=[])
    # IT-marketing can exceed httpx's 5s default → reuse the service's generous
    # read timeout (else the batch dies on a ReadTimeout mid-enrichment).
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for rec in batch:
            prospect, cost = await enrich_record(rec, client=client)
            s.est_cost_cents += cost
            if prospect is None:
                s.geo_ok += 1  # geo ran (filtered or not-found counted together)
                continue
            s.geo_ok += 1
            s.in_target += 1
            if prospect.ateco_code:
                s.enriched += 1
            if prospect.render_confidence == "high":
                s.render_high += 1
            if prospect.email or prospect.pec:
                s.with_email += 1
            s.prospects.append(prospect)
    return s
