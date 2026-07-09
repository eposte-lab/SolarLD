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

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from ..core.config import settings
from ..core.logging import get_logger
from ..data.province_centroids import province_centroid
from .energivori_contact_gate import GateResult, resolve_contact_gate
from .energivori_ingest import EnergivoroRecord
from .mapbox_service import ForwardGeocodeResult, MapboxError, forward_geocode
from .openapi_company_service import (
    _TIMEOUT,
    TARGET_PROVINCES,
    CompanyEnrichment,
    RenderSite,
    fetch_company_enrichment,
    fetch_company_geo,
    fetch_company_stakeholders,
    is_target_province,
    provinces_for_regions,
    select_render_site,
)


def _target_provinces() -> frozenset[str]:
    """The active service-area province set (Delta 2 Change A): the configured
    Centro-Sud regions, minus RM when include_roma is off. Falls back to the
    Campania default if the config resolves to nothing (mis-set regions)."""
    return (
        provinces_for_regions(
            settings.energivori_regions, include_roma=settings.energivori_include_roma
        )
        or TARGET_PROVINCES
    )


log = get_logger(__name__)

# OpenAPI.it pay-as-you-go unit costs (cents) — for the dry-run estimate.
_COST_GEO_CENTS = 5  # IT-start (cheap, on ALL vats)
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
    rec: EnergivoroRecord,
    province: str | None,
    town: str | None,
    enr: CompanyEnrichment | None,
    site: RenderSite | None,
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
    targets = _target_provinces()
    geo = await fetch_company_geo(rec.piva, client=client)
    cost = _COST_GEO_CENTS
    if geo is None:
        return None, cost
    if not is_target_province(geo.province, targets):
        return None, cost  # filtered out — no enrichment spend

    enr = await fetch_company_enrichment(rec.piva, client=client)
    cost += _COST_ENRICH_CENTS
    site = select_render_site(enr, target_provinces=targets) if enr else None
    return _to_prospect(rec, geo.province, geo.town, enr, site), cost


async def run_import(records: list[EnergivoroRecord], *, limit: int | None = None) -> ImportSummary:
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


# ---------------------------------------------------------------------------
# DB-write prep — geocode the render site + shape each prospect into a flat
# prospect_list_items dict (consumed by prospector_service.create_prospect_list_
# from_openapi). The validation backbone HARD-REQUIRES google_place_id +
# place_lat + place_lng or it marks the item 'skipped', so we geocode here.
# ---------------------------------------------------------------------------

_GEOCODE_CONCURRENCY = 6  # matches the hunter funnel's Mapbox semaphore
# Plant/HQ addresses from a VAT list are often comune-level → accept a slightly
# looser relevance than the 0.75 default, but not so loose it maps to the wrong
# building; the resolved relevance is stored per-item for downstream gating, and
# we NEVER fall back to a province centroid as the stored coordinate.
_GEOCODE_MIN_RELEVANCE = 0.6


@dataclass
class ItemPrepResult:
    items: list[dict]  # flat prospect_list_items dicts (geocoded where possible)
    geocoded: int  # items with a real coordinate + synthetic place_id
    skipped_geocode: int  # items left coordinate-less (validator will skip them)
    gate_dropped: int = 0  # items the contact gate DROPPED (won't validate/render)


def _synthetic_place_id(piva: str) -> str:
    """A stable, collision-free stand-in for the Google place_id the validator
    requires. Not a real Places id — flows into pii_hash / data_sources only."""
    return f"energivori:{piva}"


def _to_item(
    p: EnrichedProspect, geo: ForwardGeocodeResult | None, gate: GateResult | None = None
) -> dict:
    """Shape one enriched prospect into a flat prospect_list_items dict.

    On a geocode hit: sets the required trio (google_place_id + place_lat/lng)
    and stashes the geocode relevance for downstream gating. On a miss: leaves
    all three NULL so the validator transparently marks the item 'skipped'
    (never 0/0 — the skip guard checks ``is None``, not falsiness).

    When a ``gate`` is present (email_gate_enabled, Delta 2): a PASS carries the
    verified PERSONAL email as the send contact (overriding the generic OpenAPI
    one) and validates normally; a DROP is marked ``funnel_excluded_reason`` +
    ``validation_status='skipped'`` so it NEVER reaches the costly roof/render,
    while keeping the registro name + outcome for the reusable retry queue.
    """
    lat = geo.lat if geo else None
    lng = geo.lng if geo else None
    item: dict = {
        "vat_number": p.piva,
        "legal_name": p.ragione_sociale or "(Senza nome)",
        "ateco_code": p.ateco_code,
        "employees": p.employees,
        "hq_address": p.render_address,
        "hq_city": p.town,
        "hq_province": p.render_province or p.province,
        "website_domain": p.website,
        "phone": p.phone,
        # OpenAPI company email — the PRIMARY send contact for this channel
        # (validation reads it for source='openapi_it', overriding the scrape).
        "decision_maker_email": p.email,
        "decision_maker_name": None,
        "google_place_id": _synthetic_place_id(p.piva) if geo else None,
        "place_lat": lat,
        "place_lng": lng,
        "validation_status": "pending",
        "funnel_excluded_reason": None,
        "enriched_at": None,
        "enrichment_outcome": None,
        "atoka_payload": {
            "channel": "openapi_it",
            "settore_csea": p.settore_csea,
            "pec": p.pec,
            "render_confidence": p.render_confidence,
            "render_reason": p.render_reason,
            # low relevance ⇒ an approximate coordinate → gate/flag before render
            "geocode_relevance": geo.relevance if geo else None,
        },
    }
    if gate is not None:
        item["decision_maker_name"] = gate.decision_maker_name  # registro name (PASS or DROP)
        item["enriched_at"] = datetime.now(tz=UTC).isoformat()
        item["enrichment_outcome"] = {
            "passed": gate.passed,
            "email_status": gate.email_status,
            "email_confidence": gate.email_confidence,
            "email_source": gate.email_source,
            "excluded_reason": gate.excluded_reason,
            "decision_maker_source": gate.decision_maker_source,
            "candidates": gate.candidates,
        }
        if gate.passed:
            # the verified personal email becomes the send contact
            item["decision_maker_email"] = gate.email
        else:
            # DROP — keep the generic email for audit, but never validate/render
            item["funnel_excluded_reason"] = gate.excluded_reason
            item["validation_status"] = "skipped"
    return item


async def _geocode_one(
    p: EnrichedProspect, *, client: httpx.AsyncClient
) -> ForwardGeocodeResult | None:
    addr = p.render_address
    if not addr:
        return None
    prov = (p.render_province or p.province or "").upper()
    proximity = province_centroid(prov)  # bias the search near the plant's province
    try:
        return await forward_geocode(
            addr,
            proximity=proximity,
            min_relevance=_GEOCODE_MIN_RELEVANCE,
            client=client,
        )
    except (MapboxError, httpx.HTTPError):
        # A transient Mapbox/network failure must NOT abort the batch (the
        # OpenAPI enrichment is already paid) → treat it as a per-item miss.
        log.warning("energivori.geocode_error", piva=p.piva)
        return None


async def _gate_one(p: EnrichedProspect, *, client: httpx.AsyncClient) -> GateResult | None:
    """Run the contact GATE for one prospect (Delta 2) — None when the gate is
    off. Fetches the registro managers + resolves the personal-email decision."""
    if not settings.email_gate_enabled:
        return None
    try:
        managers = await fetch_company_stakeholders(p.piva, client=client)
        return await resolve_contact_gate(
            email=p.email,
            website=p.website,
            managers=managers,
            client=client,
            acceptall_as_medium=settings.acceptall_as_medium_confidence,
        )
    except Exception as exc:  # noqa: BLE001 — a gate error must not abort the batch
        log.warning("energivori.gate_error", piva=p.piva, err=type(exc).__name__)
        return None


async def prepare_items(
    prospects: list[EnrichedProspect], *, client: httpx.AsyncClient
) -> ItemPrepResult:
    """Geocode + (Delta 2) GATE each prospect, then shape flat item dicts.

    Concurrency-bounded; a miss/leaked error yields a coordinate-less item rather
    than aborting the run. With email_gate_enabled, DROPs are marked + kept but
    validation_status='skipped' so they never reach the costly roof/render.
    """
    sem = asyncio.Semaphore(_GEOCODE_CONCURRENCY)

    async def _one(p: EnrichedProspect) -> tuple[ForwardGeocodeResult | None, GateResult | None]:
        async with sem:
            geo = await _geocode_one(p, client=client)
            gate = await _gate_one(p, client=client)
            return geo, gate

    results = await asyncio.gather(
        *(_one(p) for p in prospects),
        return_exceptions=True,  # belt-and-suspenders: one bad row never kills the batch
    )
    pairs: list[tuple[ForwardGeocodeResult | None, GateResult | None]] = [
        (None, None) if isinstance(r, BaseException) else r for r in results
    ]
    items = [_to_item(p, geo, gate) for p, (geo, gate) in zip(prospects, pairs, strict=True)]
    geocoded = sum(1 for geo, _ in pairs if geo is not None)
    gate_dropped = sum(1 for _, gate in pairs if gate is not None and not gate.passed)
    return ItemPrepResult(
        items=items,
        geocoded=geocoded,
        skipped_geocode=len(items) - geocoded,
        gate_dropped=gate_dropped,
    )
