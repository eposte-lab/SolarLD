"""Tests for sector_target_service — pure cache+lookup logic.

We use a fake Supabase client that returns a hand-rolled `ateco_google_types`
result set so the tests don't need a live DB. The service caches once
across tests, so each test resets the cache via ``_reset_cache_for_tests``.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.services import sector_target_service as sts


# ---------------------------------------------------------------------------
# Fake Supabase
# ---------------------------------------------------------------------------


class _FakeSelect:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def select(self, _columns: str) -> "_FakeSelect":
        return self

    def execute(self) -> Any:
        class _Res:
            data = self._rows
        return _Res()


class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def table(self, name: str) -> _FakeSelect:
        assert name == "ateco_google_types"
        return _FakeSelect(self._rows)


# ---------------------------------------------------------------------------
# Fixture: standard 3-group palette
# ---------------------------------------------------------------------------


def _seed_rows() -> list[dict[str, Any]]:
    return [
        # industry_heavy: 25.11 + 24.10
        {
            "ateco_code": "25.11",
            "wizard_group": "industry_heavy",
            "osm_landuse_hints": [{"landuse": "industrial", "weight": 1.0}],
            "osm_additional_tags": [{"man_made": "works", "weight": 0.9}],
            "places_keywords": ["carpenteria metallica", "officina meccanica industriale"],
            "places_excluded_types": ["car_repair", "car_dealer"],
            "site_signal_keywords": ["capannone", "stabilimento", "metalmeccanico"],
            "min_zone_area_m2": 5000,
            "search_radius_m": 1500,
            "typical_kwp_range_min": 100,
            "typical_kwp_range_max": 500,
        },
        {
            "ateco_code": "24.10",
            "wizard_group": "industry_heavy",
            "osm_landuse_hints": [{"landuse": "industrial", "weight": 1.0}],
            "osm_additional_tags": [{"industrial": "factory", "weight": 0.9}],
            "places_keywords": ["acciaieria", "siderurgia"],
            "places_excluded_types": ["car_repair"],
            "site_signal_keywords": ["acciaieria", "siderurgia"],
            "min_zone_area_m2": 8000,  # larger
            "search_radius_m": 1500,
            "typical_kwp_range_min": 200,
            "typical_kwp_range_max": 1000,
        },
        # logistics: 52.10
        {
            "ateco_code": "52.10",
            "wizard_group": "logistics",
            "osm_landuse_hints": [{"landuse": "industrial", "weight": 1.0}],
            "osm_additional_tags": [{"building": "warehouse", "weight": 1.0}],
            "places_keywords": ["centro logistico", "magazzino industriale"],
            "places_excluded_types": ["storage"],
            "site_signal_keywords": ["logistica", "magazzino", "spedizione"],
            "min_zone_area_m2": 8000,
            "search_radius_m": 2500,
            "typical_kwp_range_min": 150,
            "typical_kwp_range_max": 800,
        },
        # hospitality_large: 55.10.10
        {
            "ateco_code": "55.10.10",
            "wizard_group": "hospitality_large",
            "osm_landuse_hints": [{"landuse": "commercial", "weight": 0.5}],
            "osm_additional_tags": [{"tourism": "hotel", "weight": 1.0}],
            "places_keywords": ["hotel 4 stelle", "resort"],
            "places_excluded_types": ["bed_and_breakfast"],
            "site_signal_keywords": ["hotel", "resort", "suite"],
            "min_zone_area_m2": 1000,
            "search_radius_m": 800,
            "typical_kwp_range_min": 60,
            "typical_kwp_range_max": 250,
        },
    ]


@pytest.fixture(autouse=True)
def _reset_cache():
    sts._reset_cache_for_tests()
    yield
    sts._reset_cache_for_tests()


@pytest.fixture()
def fake_sb():
    return _FakeSupabase(_seed_rows())


# ---------------------------------------------------------------------------
# get_sector_config_by_wizard_group
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sector_config_merges_two_atecos_of_same_group(fake_sb):
    mapping = await sts.get_sector_config_by_wizard_group(
        fake_sb, wizard_group="industry_heavy"
    )
    assert mapping is not None
    assert mapping.wizard_group == "industry_heavy"
    # ATECO codes preserved in seed order, deduped
    assert "25.11" in mapping.ateco_codes
    assert "24.10" in mapping.ateco_codes
    # site_signal_keywords union'd (capannone from 25.11, acciaieria from 24.10)
    assert "capannone" in mapping.site_signal_keywords
    assert "acciaieria" in mapping.site_signal_keywords
    # min_zone_area_m2 takes the smaller value (most permissive)
    assert mapping.min_zone_area_m2 == 5000
    # typical_kwp_range_max takes the larger value (broadest)
    assert mapping.typical_kwp_range_max == 1000
    assert mapping.typical_kwp_range_min == 100  # the smaller


@pytest.mark.asyncio
async def test_get_sector_config_unknown_returns_none(fake_sb):
    assert await sts.get_sector_config_by_wizard_group(
        fake_sb, wizard_group="does_not_exist"
    ) is None


@pytest.mark.asyncio
async def test_osm_hints_parsed(fake_sb):
    mapping = await sts.get_sector_config_by_wizard_group(
        fake_sb, wizard_group="logistics"
    )
    assert mapping is not None
    assert len(mapping.osm_landuse_hints) == 1
    hint = mapping.osm_landuse_hints[0]
    assert hint.tag_key == "landuse"
    assert hint.tag_value == "industrial"
    assert hint.weight == 1.0
    # Additional tag uses 'building' key
    assert any(
        h.tag_key == "building" and h.tag_value == "warehouse"
        for h in mapping.osm_additional_tags
    )


# ---------------------------------------------------------------------------
# derive_ateco_whitelist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_derive_whitelist_unions_groups(fake_sb):
    wl = await sts.derive_ateco_whitelist(
        fake_sb, wizard_groups=["industry_heavy", "logistics"]
    )
    assert set(wl) == {"25.11", "24.10", "52.10"}


@pytest.mark.asyncio
async def test_derive_whitelist_skips_unknown_group(fake_sb):
    wl = await sts.derive_ateco_whitelist(
        fake_sb, wizard_groups=["industry_heavy", "does_not_exist"]
    )
    assert set(wl) == {"25.11", "24.10"}


@pytest.mark.asyncio
async def test_derive_whitelist_empty_input(fake_sb):
    assert await sts.derive_ateco_whitelist(fake_sb, wizard_groups=[]) == []


# ---------------------------------------------------------------------------
# predict_sector_for_candidate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_predict_exact_ateco_match(fake_sb):
    pred = await sts.predict_sector_for_candidate(
        fake_sb,
        ateco_code="25.11",
        business_name="Carpenteria Rossi Srl",
        enabled_wizard_groups=["industry_heavy", "logistics"],
    )
    assert pred == ("industry_heavy", 1.0)


@pytest.mark.asyncio
async def test_predict_exact_ateco_outside_enabled_groups_falls_through_to_prefix(fake_sb):
    """If exact ATECO maps to a group the tenant hasn't enabled, we
    don't return it. The prefix path may still match — and 25.11 has
    prefix '25' which only matches industry_heavy in the seed, so it
    too is filtered out. Result: None."""
    pred = await sts.predict_sector_for_candidate(
        fake_sb,
        ateco_code="25.11",
        business_name=None,
        enabled_wizard_groups=["logistics"],  # industry_heavy NOT enabled
    )
    assert pred is None


@pytest.mark.asyncio
async def test_predict_prefix_match(fake_sb):
    """ATECO 25.99 isn't in the seed but 25.x is industry_heavy."""
    pred = await sts.predict_sector_for_candidate(
        fake_sb,
        ateco_code="25.99",
        business_name=None,
        enabled_wizard_groups=["industry_heavy", "logistics"],
    )
    assert pred is not None
    wg, conf = pred
    assert wg == "industry_heavy"
    assert conf == 0.7


@pytest.mark.asyncio
async def test_predict_fuzzy_business_name(fake_sb):
    """No ATECO at all, but the business name matches industry_heavy
    site_signal_keywords."""
    pred = await sts.predict_sector_for_candidate(
        fake_sb,
        ateco_code=None,
        business_name="Capannone Industriale Brescia Srl",
        enabled_wizard_groups=["industry_heavy", "hospitality_large"],
    )
    assert pred is not None
    wg, conf = pred
    assert wg == "industry_heavy"
    assert conf == 0.4


@pytest.mark.asyncio
async def test_predict_no_match_returns_none(fake_sb):
    pred = await sts.predict_sector_for_candidate(
        fake_sb,
        ateco_code="99.99",
        business_name="Studio Notarile Brescia",
        enabled_wizard_groups=["industry_heavy", "logistics"],
    )
    assert pred is None


@pytest.mark.asyncio
async def test_predict_empty_enabled_groups_returns_none(fake_sb):
    assert await sts.predict_sector_for_candidate(
        fake_sb,
        ateco_code="25.11",
        business_name="X",
        enabled_wizard_groups=[],
    ) is None


# ---------------------------------------------------------------------------
# union_site_signal_keywords
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_union_keywords_dedupes_across_groups(fake_sb):
    kws = await sts.union_site_signal_keywords(
        fake_sb, wizard_groups=["industry_heavy", "logistics"]
    )
    # capannone, stabilimento, metalmeccanico from industry_heavy
    # acciaieria, siderurgia from industry_heavy (different ATECO)
    # logistica, magazzino, spedizione from logistics
    assert "capannone" in kws
    assert "logistica" in kws
    assert "acciaieria" in kws
    # No duplicates
    assert len(kws) == len(set(kws))


@pytest.mark.asyncio
async def test_get_wizard_group_for_ateco_direct_lookup(fake_sb):
    assert await sts.get_wizard_group_for_ateco(fake_sb, ateco_code="52.10") == "logistics"
    assert await sts.get_wizard_group_for_ateco(fake_sb, ateco_code="99.99") is None
    assert await sts.get_wizard_group_for_ateco(fake_sb, ateco_code="") is None
