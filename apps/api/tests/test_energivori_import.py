"""Energivori import orchestration — the pure record → prospect mapping.

Covers ``_to_prospect`` (the flat merge that feeds the prospect_list backbone):
both the enriched path and the enrichment-failed fallback. The async fetch
chain (``enrich_record`` / ``run_import``) is exercised live against
company.openapi.com, so only the pure mapping is unit-tested here.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx

from src.services import energivori_import_service as eis
from src.services.energivori_import_service import (
    EnrichedProspect,
    _to_item,
    _to_prospect,
    prepare_items,
)
from src.services.energivori_ingest import EnergivoroRecord
from src.services.openapi_company_service import CompanyEnrichment, RenderSite


def _rec() -> EnergivoroRecord:
    return EnergivoroRecord(
        piva="02334410657",
        ragione_sociale="CAMPANIA PLASTICA SRL",
        settore="Plastica / gomma",
    )


def test_to_prospect_merges_enrichment_and_site() -> None:
    enr = CompanyEnrichment(
        piva="02334410657",
        phone="081949586",
        email="info@campaniaplastica.com",
        pec="CAMPANIAPLASTICA@PEC.IT",
        website="www.campaniaplastica.it",
        ateco_code="222",
        employees=27,
    )
    site = RenderSite(
        address_line="CONTRADA PONTE VALENTINO, 3, 82100, BENEVENTO, BN",
        province="BN",
        confidence="high",
        reason="productive_local_unit_in_region",
    )
    p = _to_prospect(_rec(), "SA", "ANGRI", enr, site)
    assert p.piva == "02334410657"
    assert p.ragione_sociale == "CAMPANIA PLASTICA SRL"
    assert p.province == "SA"  # registered/geo province (the CSEA targeting reason)
    assert p.town == "ANGRI"
    assert p.settore_csea == "Plastica / gomma"
    assert p.phone == "081949586"
    assert p.email == "info@campaniaplastica.com"
    assert p.pec == "CAMPANIAPLASTICA@PEC.IT"
    assert p.website == "www.campaniaplastica.it"
    assert p.ateco_code == "222"
    assert p.employees == 27
    # the render points at the IN-REGION plant, not the registered office
    assert (p.render_address or "").startswith("CONTRADA PONTE VALENTINO")
    assert p.render_province == "BN"
    assert p.render_confidence == "high"
    assert p.render_reason == "productive_local_unit_in_region"


def test_to_prospect_without_enrichment_is_low_and_empty() -> None:
    # Enrichment failed (None) → contacts empty; render falls back to the geo
    # province with low confidence so the creative gate skips it.
    p = _to_prospect(_rec(), "SA", "ANGRI", None, None)
    assert p.phone is None
    assert p.email is None
    assert p.pec is None
    assert p.ateco_code is None
    assert p.render_address is None
    assert p.render_province == "SA"  # falls back to the geo province
    assert p.render_confidence == "low"
    assert p.render_reason == "not_enriched"


def _prospect(piva: str = "02334410657", **kw: object) -> EnrichedProspect:
    base: dict[str, object] = {
        "piva": piva,
        "ragione_sociale": "CAMPANIA PLASTICA SRL",
        "province": "SA",
        "town": "ANGRI",
        "settore_csea": "Plastica / gomma",
        "email": "info@campaniaplastica.com",
        "pec": "CAMPANIAPLASTICA@PEC.IT",
        "website": "www.campaniaplastica.it",
        "ateco_code": "222",
        "employees": 27,
        "render_address": "CONTRADA PONTE VALENTINO, 3, 82100, BENEVENTO, BN",
        "render_province": "BN",
        "render_confidence": "high",
        "render_reason": "productive_local_unit_in_region",
    }
    base.update(kw)
    return EnrichedProspect(**base)  # type: ignore[arg-type]


def _geo(lat: float, lng: float, relevance: float = 0.9) -> SimpleNamespace:
    return SimpleNamespace(lat=lat, lng=lng, relevance=relevance)


def test_to_item_geocoded_sets_required_trio() -> None:
    item = _to_item(_prospect(), _geo(41.13, 14.78, relevance=0.82))
    assert item["vat_number"] == "02334410657"
    assert item["legal_name"] == "CAMPANIA PLASTICA SRL"
    # the validator's required trio, all present on a geocode hit
    assert item["google_place_id"] == "energivori:02334410657"
    assert item["place_lat"] == 41.13
    assert item["place_lng"] == 14.78
    assert item["hq_province"] == "BN"  # the in-region plant province
    assert item["decision_maker_email"] == "info@campaniaplastica.com"
    assert item["validation_status"] == "pending"
    # forensics stashed in atoka_payload (no dedicated columns for these)
    assert item["atoka_payload"]["channel"] == "openapi_it"
    assert item["atoka_payload"]["pec"] == "CAMPANIAPLASTICA@PEC.IT"
    assert item["atoka_payload"]["settore_csea"] == "Plastica / gomma"
    assert item["atoka_payload"]["render_confidence"] == "high"
    assert item["atoka_payload"]["geocode_relevance"] == 0.82


def test_to_item_no_coord_leaves_trio_null() -> None:
    # Geocode miss → the required trio is NULL (never 0/0), so the validator
    # transparently 'skip's it; identity + contact still captured for audit.
    item = _to_item(_prospect(), None)
    assert item["google_place_id"] is None
    assert item["place_lat"] is None
    assert item["place_lng"] is None
    assert item["atoka_payload"]["geocode_relevance"] is None
    assert item["vat_number"] == "02334410657"
    assert item["decision_maker_email"] == "info@campaniaplastica.com"


def test_prepare_items_geocodes_and_counts(monkeypatch) -> None:
    hit = _prospect(piva="02334410657")
    miss = _prospect(
        piva="02063170613",
        ragione_sociale="EUROFRIGO SPA",
        render_address="VIA IGNOTA, COMUNE X",
        render_province="CE",
    )

    async def fake_geocode(address: str, **_: object):
        return _geo(41.0, 14.0) if "PONTE VALENTINO" in address else None

    monkeypatch.setattr(eis, "forward_geocode", fake_geocode)

    async def run() -> object:
        async with httpx.AsyncClient() as client:
            return await prepare_items([hit, miss], client=client)

    res = asyncio.run(run())
    assert res.geocoded == 1
    assert res.skipped_geocode == 1
    # order preserved: item 0 is the hit, item 1 the miss
    assert res.items[0]["google_place_id"] == "energivori:02334410657"
    assert res.items[0]["place_lat"] == 41.0
    assert res.items[1]["google_place_id"] is None
    assert res.items[1]["place_lat"] is None


def test_prepare_items_survives_geocoder_exception(monkeypatch) -> None:
    # A leaked geocoder exception (not just MapboxError) must not abort the batch
    # — the row is treated as a miss so the paid enrichment isn't wasted.
    good = _prospect(piva="02334410657")
    boom = _prospect(piva="02063170613", render_address="VIA BOOM")

    async def flaky_geocode(address: str, **_: object):
        if "BOOM" in address:
            raise RuntimeError("mapbox exploded")
        return _geo(41.0, 14.0)

    monkeypatch.setattr(eis, "forward_geocode", flaky_geocode)

    async def run() -> object:
        async with httpx.AsyncClient() as client:
            return await prepare_items([good, boom], client=client)

    res = asyncio.run(run())
    assert res.geocoded == 1
    assert res.skipped_geocode == 1
    assert res.items[0]["place_lat"] == 41.0
    assert res.items[1]["place_lat"] is None
