"""OpenAPI.it Company enrichment — parse + productive-site selection.

Fixtures are TRIMMED but REAL responses captured from company.openapi.com
(3T Trattamenti Termici, P.IVA 00767880016), so the camelCase field mappings
are locked against the live shape.
"""

from __future__ import annotations

from src.services.openapi_company_service import (
    CompanyEnrichment,
    parse_it_marketing,
    select_render_site,
)

# Real IT-marketing shape (data = DICT).
_IT_MARKETING = {
    "data": {
        "companyDetails": {
            "companyName": "3T TRATTAMENTI TERMICI TORINO SRL",
            "taxCode": "00767880016",
            "vatCode": "00767880016",
        },
        "contacts": {"fax": "0119592439", "telephoneNumber": "0119576428"},
        "atecoClassification": {
            "ateco": {"code": "255", "description": "Treatment and coating of metals"},
            "firstLevel": {"ateco": {"code": "C", "description": "C - Manufacturing"}},
            "ateco2022": {"code": "2561"},
        },
        "pec": "3TTRATTAMENTITERMICITORINO@PECSOCI.UI.TORINO.IT",
        "mail": {"email": "info@3tsrl.it"},
        "webAndSocial": {"website": "www.3tsrl.it", "hasSocial": False},
        "employees": {"employee": 30, "employeeRange": {"code": "ER5"}},
        "allOffices": [
            {
                "companyDetails": {"officeType": {"code": "SSL", "description": "registered office"}},
                "address": {"zipCode": "10098", "province": {"code": "TO"},
                            "streetName": "VIA VAJONT, 77", "town": "RIVOLI"},
            },
            {
                "companyDetails": {"officeType": {"code": "UL", "description": "Local units"}},
                "address": {"zipCode": "10098", "province": {"code": "TO"},
                            "streetName": "VIA ALESSANDRIA, 5", "town": "RIVOLI"},
            },
        ],
    }
}


def test_parse_it_marketing_maps_real_fields() -> None:
    enr = parse_it_marketing(_IT_MARKETING, "00767880016")
    assert enr is not None
    assert enr.company_name == "3T TRATTAMENTI TERMICI TORINO SRL"
    assert enr.phone == "0119576428"
    assert enr.email == "info@3tsrl.it"
    assert enr.pec.endswith("TORINO.IT")
    assert enr.website == "www.3tsrl.it"
    assert enr.ateco_code == "255"
    assert enr.ateco_macro == "C"
    assert enr.is_productive is True  # C = manufacturing
    assert enr.employees == 30
    assert enr.province == "TO"  # from the registered (SSL) office
    assert len(enr.offices) == 2
    assert sum(o.is_local_unit for o in enr.offices) == 1


def test_select_render_site_prefers_productive_local_unit() -> None:
    enr = parse_it_marketing(_IT_MARKETING, "00767880016")
    assert enr is not None
    site = select_render_site(enr)
    assert site.confidence == "high"
    assert site.reason == "productive_local_unit"
    assert "VIA ALESSANDRIA" in (site.address_line or "")  # the UL, not the registered office
    assert site.province == "TO"


def test_non_productive_company_is_low_confidence() -> None:
    # A retail/office company (macro G) → not a plant → flag for manual review.
    enr = CompanyEnrichment(
        piva="00000000000",
        ateco_macro="G",
        offices=parse_it_marketing(_IT_MARKETING, "x").offices,  # reuse addresses
    )
    assert enr.is_productive is False
    site = select_render_site(enr)
    assert site.confidence == "low"
    assert site.reason == "non_productive_ateco"


def test_parse_handles_missing_and_list_data() -> None:
    assert parse_it_marketing(None, "x") is None
    assert parse_it_marketing({"data": None}, "x") is None
    # IT-start style: data is a LIST → first record is used.
    listy = {"data": [{"companyDetails": {"companyName": "ACME"}}]}
    enr = parse_it_marketing(listy, "x")
    assert enr is not None
    assert enr.company_name == "ACME"
