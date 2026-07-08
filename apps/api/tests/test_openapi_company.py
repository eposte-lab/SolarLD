"""OpenAPI.it Company enrichment — parse + productive-site selection.

Fixtures are TRIMMED but REAL responses captured from company.openapi.com
(3T Trattamenti Termici, P.IVA 00767880016), so the camelCase field mappings
are locked against the live shape.
"""

from __future__ import annotations

from src.services.openapi_company_service import (
    CompanyEnrichment,
    is_target_province,
    parse_it_marketing,
    parse_it_start,
    select_render_site,
)

# Real IT-start shape (data = LIST; registeredOffice.province is a plain string;
# gps.coordinates is [lng, lat]).
_IT_START = {
    "data": [
        {
            "taxCode": "00767880016",
            "vatCode": "00767880016",
            "companyName": "3T - TRATTAMENTI TERMICI TORINO - S.R.L.",
            "address": {
                "registeredOffice": {
                    "streetName": "VIA VAJONT 77", "town": "RIVOLI", "province": "TO",
                    "zipCode": "10098", "gps": {"coordinates": [7.55672, 45.07899]},
                }
            },
            "activityStatus": "ATTIVA",
        }
    ]
}

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


def test_select_render_site_prefers_in_region_productive_unit() -> None:
    enr = parse_it_marketing(_IT_MARKETING, "00767880016")
    assert enr is not None
    # Installer serves TO → the TO local unit is the plant to render.
    site = select_render_site(enr, target_provinces=frozenset({"TO"}))
    assert site.confidence == "high"
    assert site.reason == "productive_local_unit_in_region"
    assert "VIA ALESSANDRIA" in (site.address_line or "")  # the UL, not the registered office
    assert site.province == "TO"


def test_render_site_out_of_region_is_flagged() -> None:
    # Same productive company, but the installer serves NA — the TO plant is
    # out of the service area → low confidence, so the render gate skips it.
    enr = parse_it_marketing(_IT_MARKETING, "00767880016")
    assert enr is not None
    site = select_render_site(enr, target_provinces=frozenset({"NA"}))
    assert site.confidence == "low"
    assert site.reason == "productive_out_of_region"


def test_non_productive_company_is_low_confidence() -> None:
    # A retail/office company (macro G) → not a plant → flag for manual review.
    enr = CompanyEnrichment(
        piva="00000000000",
        ateco_macro="G",
        offices=parse_it_marketing(_IT_MARKETING, "x").offices,  # reuse addresses
    )
    assert enr.is_productive is False
    site = select_render_site(enr, target_provinces=frozenset({"TO"}))
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


def test_parse_it_start_geo() -> None:
    geo = parse_it_start(_IT_START, "00767880016")
    assert geo is not None
    assert geo.province == "TO"
    assert geo.town == "RIVOLI"
    assert geo.lat == 45.07899  # [lng, lat] → lat is the 2nd coordinate
    assert geo.lng == 7.55672
    assert geo.activity_status == "ATTIVA"
    assert parse_it_start(None, "x") is None


def test_is_target_province_campania_filter() -> None:
    assert is_target_province("NA") is True
    assert is_target_province("na") is True  # case-insensitive
    assert is_target_province("SA") is True
    assert is_target_province("TO") is False  # 3T is in Piemonte → filtered out
    assert is_target_province(None) is False
