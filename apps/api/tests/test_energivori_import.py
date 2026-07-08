"""Energivori import orchestration — the pure record → prospect mapping.

Covers ``_to_prospect`` (the flat merge that feeds the prospect_list backbone):
both the enriched path and the enrichment-failed fallback. The async fetch
chain (``enrich_record`` / ``run_import``) is exercised live against
company.openapi.com, so only the pure mapping is unit-tested here.
"""

from __future__ import annotations

from src.services.energivori_import_service import _to_prospect
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
