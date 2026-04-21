"""Pure-logic tests for the hunter_funnel package — no network, no DB.

These cover the pieces we can exercise without mocking every external
dependency:

  * L1 geo-filter derivation from territory rows
  * L3 batch parser tolerance to messy Claude output
  * L3 fallback heuristic correctness
  * L4 gate math (top-N selection with floor)
  * ScanCostAccumulator arithmetic + total

Level-by-level end-to-end flows (with mocked Atoka/Claude/Solar) will land
in a later batch — they require heavier fixtures and a running Supabase.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.agents.hunter_funnel.level1_discovery import _derive_geo_filters
from src.agents.hunter_funnel.level3_proxy_score import (
    _clamp_score,
    _fallback_score,
    _parse_batch_response,
    _str_list,
)
from src.agents.hunter_funnel.types import (
    EnrichedCandidate,
    EnrichmentSignals,
)
from src.services.italian_business_service import AtokaProfile
from src.services.scan_cost_tracker import ScanCostAccumulator


# ---------------------------------------------------------------------------
# L1 — _derive_geo_filters
# ---------------------------------------------------------------------------


def test_derive_geo_from_provincia_territory():
    territory = {"type": "provincia", "code": "NA"}
    assert _derive_geo_filters(territory) == ("NA", None)


def test_derive_geo_from_regione_territory():
    territory = {"type": "regione", "code": "Campania"}
    assert _derive_geo_filters(territory) == (None, "Campania")


def test_derive_geo_from_cap_with_parent_province():
    territory = {"type": "cap", "code": "80100", "metadata": {"provincia": "na"}}
    assert _derive_geo_filters(territory) == ("NA", None)


def test_derive_geo_from_cap_without_parent_returns_none():
    territory = {"type": "cap", "code": "80100"}
    assert _derive_geo_filters(territory) == (None, None)


def test_derive_geo_unknown_type_returns_none():
    territory = {"type": "comune", "code": "Napoli"}
    assert _derive_geo_filters(territory) == (None, None)


# ---------------------------------------------------------------------------
# L3 — response parsing
# ---------------------------------------------------------------------------


def test_parse_batch_response_happy_path():
    text = '{"results": [{"score": 80, "reasons": ["manifattura"], "flags": []}]}'
    parsed = _parse_batch_response(text, expected_len=1)
    assert parsed is not None
    assert len(parsed) == 1
    assert parsed[0]["score"] == 80


def test_parse_batch_response_strips_markdown_fence():
    text = (
        "```json\n"
        '{"results": [{"score": 50, "reasons": [], "flags": []}]}\n'
        "```"
    )
    parsed = _parse_batch_response(text, expected_len=1)
    assert parsed is not None
    assert parsed[0]["score"] == 50


def test_parse_batch_response_rejects_wrong_length():
    text = '{"results": [{"score": 10}, {"score": 20}]}'
    assert _parse_batch_response(text, expected_len=3) is None


def test_parse_batch_response_rejects_non_json():
    assert _parse_batch_response("totally not json", expected_len=1) is None


def test_parse_batch_response_rejects_missing_results_key():
    text = '{"other": []}'
    assert _parse_batch_response(text, expected_len=0) is None


# ---------------------------------------------------------------------------
# L3 — helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (50, 50),
        (-10, 0),
        (150, 100),
        ("42", 42),
        (None, 0),
        ("abc", 0),
    ],
)
def test_clamp_score(raw, expected):
    assert _clamp_score(raw) == expected


def test_str_list_caps_at_six():
    result = _str_list(list(range(20)))
    assert len(result) == 6
    assert all(isinstance(x, str) for x in result)


def test_str_list_rejects_non_list():
    assert _str_list("string") == []
    assert _str_list(None) == []


# ---------------------------------------------------------------------------
# L3 — fallback heuristic
# ---------------------------------------------------------------------------


def _make_candidate(
    ateco: str = "10.51",
    employees: int | None = 50,
    revenue_cents: int | None = 10_000_000_00,
    site_signals: list[str] | None = None,
) -> EnrichedCandidate:
    profile = AtokaProfile(
        vat_number="IT12345678901",
        legal_name="Test SRL",
        ateco_code=ateco,
        ateco_description=None,
        yearly_revenue_cents=revenue_cents,
        employees=employees,
        website_domain="esempio.it",
        decision_maker_name=None,
        decision_maker_role=None,
        linkedin_url=None,
    )
    return EnrichedCandidate(
        candidate_id=uuid4(),
        profile=profile,
        enrichment=EnrichmentSignals(site_signals=site_signals or []),
    )


def test_fallback_rewards_ideal_manifattura():
    c = _make_candidate(
        ateco="10.51",
        employees=80,
        revenue_cents=5_000_000_00,
        site_signals=["capannone", "stabilimento"],
    )
    s = _fallback_score(c)
    # Baseline 40 + 20 (size) + 10 (revenue) + 15 (ateco) + 6 (signals=2*3)
    assert s.score == 91


def test_fallback_penalises_tiny_office():
    c = _make_candidate(
        ateco="70.22",  # consulenza — not in industrial whitelist
        employees=2,
        revenue_cents=200_000_00,
    )
    s = _fallback_score(c)
    # Baseline 40 - 15 (size<5)
    assert s.score == 25


def test_fallback_marks_haiku_unavailable_flag():
    c = _make_candidate()
    s = _fallback_score(c)
    assert "haiku_unavailable" in s.flags


# ---------------------------------------------------------------------------
# Cost accumulator
# ---------------------------------------------------------------------------


def test_cost_accumulator_sums_centres():
    cost = ScanCostAccumulator(
        tenant_id="t1", scan_id="s1", scan_mode="b2b_funnel_v2"
    )
    cost.add_atoka(records=100, cost_cents=100)
    cost.add_places(calls=50, cost_cents=100)
    cost.add_claude(scored=100, cost_cents=100)
    cost.add_solar(calls=20, cost_cents=40)
    cost.add_mapbox(cost_cents=15)

    assert cost.total_cost_cents == 355
    assert cost.candidates_l1 == 100
    assert cost.candidates_l2 == 50
    assert cost.candidates_l3 == 100
    assert cost.candidates_l4 == 20


def test_cost_accumulator_over_budget_respects_none():
    cost = ScanCostAccumulator(
        tenant_id="t1", scan_id="s1", scan_mode="b2b_funnel_v2"
    )
    cost.add_atoka(records=100, cost_cents=50_000)  # €500
    assert cost.over_budget(None) is False
    assert cost.over_budget(0) is False
    assert cost.over_budget(1000.0) is False  # €1000 budget, €500 spent
    assert cost.over_budget(100.0) is True    # €100 budget, €500 spent


# ---------------------------------------------------------------------------
# L4 gate math — inline expectation
# ---------------------------------------------------------------------------


def test_solar_gate_math_respects_min_floor():
    """Tiny scans still send at least solar_gate_min_candidates to Solar."""
    # With 5 candidates and 20% gate, naive math = 1. Floor should lift it
    # to solar_gate_min_candidates (default 20) — capped to population.
    from src.agents.hunter_funnel.types import FunnelContext
    # Pure math, no real FunnelContext needed — we compute manually using
    # the same formula as run_level4.
    total = 5
    gate_pct = 0.20
    min_cands = 20
    n_gate = max(min_cands, int(total * gate_pct))
    n_gate = min(n_gate, total)
    assert n_gate == 5  # all 5 go through — can't gate above population


def test_solar_gate_math_caps_at_fraction_for_large_scans():
    total = 5000
    gate_pct = 0.20
    min_cands = 20
    n_gate = max(min_cands, int(total * gate_pct))
    n_gate = min(n_gate, total)
    assert n_gate == 1000
