"""Unit tests for `tenant_config_service._parse_row` and its helpers.

The DAO round-trips Supabase JSON rows into a typed `TenantConfig`.
These tests feed hand-built rows (no Supabase client involved) and
assert the dataclass carries the expected typed values, with null /
missing fields falling back to safe defaults.

Covered:
  - `_parse_row`: full row, sparse row, ISO timestamp with/without `Z`
  - `TechnicalFilters.from_dict`: missing keys → defaults
  - `_default_for`: returns safe opportunistic defaults (wizard pending)
"""

from __future__ import annotations

from datetime import timezone
from uuid import UUID

from src.services.tenant_config_service import (
    TechnicalFilters,
    _default_for,
    _parse_row,
)

TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")


# ---------------------------------------------------------------------------
# TechnicalFilters.from_dict
# ---------------------------------------------------------------------------


def test_technical_filters_defaults_when_empty() -> None:
    tf = TechnicalFilters.from_dict({})
    assert tf.min_area_sqm == 0.0
    assert tf.min_kwp == 0.0
    # max_shading defaults to fully-permissive (1.0 = no rejection)
    assert tf.max_shading == 1.0
    assert tf.min_exposure_score == 0.0


def test_technical_filters_parses_numeric_types() -> None:
    tf = TechnicalFilters.from_dict(
        {
            "min_area_sqm": "500",  # strings from JSON coerce via float()
            "min_kwp": 50,
            "max_shading": 0.4,
            "min_exposure_score": 0.7,
        }
    )
    assert tf.min_area_sqm == 500.0
    assert tf.min_kwp == 50.0
    assert tf.max_shading == 0.4
    assert tf.min_exposure_score == 0.7


# ---------------------------------------------------------------------------
# _parse_row — full row
# ---------------------------------------------------------------------------


def _full_row() -> dict:
    return {
        "tenant_id": str(TENANT_ID),
        "scan_mode": "b2b_precision",
        "target_segments": ["b2b"],
        "place_type_whitelist": ["supermarket", "hardware_store"],
        "place_type_priority": {"supermarket": 10, "hardware_store": 7},
        "ateco_whitelist": ["47.11", "47.52"],
        "ateco_blacklist": [],
        "ateco_priority": {"47.11": 10},
        "min_employees": 10,
        "max_employees": 500,
        "min_revenue_eur": 1_000_000,
        "max_revenue_eur": 50_000_000,
        "technical_filters": {
            "b2b": {"min_area_sqm": 500, "min_kwp": 50, "max_shading": 0.4, "min_exposure_score": 0.7},
            "b2c": {"min_area_sqm": 60, "min_kwp": 3, "max_shading": 0.5, "min_exposure_score": 0.6},
        },
        "scoring_threshold": 70,
        "scoring_weights": {
            "b2b": {"kwp": 30, "consumption": 20, "solvency": 20, "incentives": 15, "distance": 15},
        },
        "monthly_scan_budget_eur": 1500.0,
        "monthly_outreach_budget_eur": 2000.0,
        "scan_priority_zones": ["capoluoghi", "costa"],
        "scan_grid_density_m": 25,
        "atoka_enabled": True,
        "atoka_monthly_cap_eur": 200.0,
        "wizard_completed_at": "2026-04-01T10:30:00+00:00",
    }


def test_parse_row_full_body() -> None:
    cfg = _parse_row(_full_row())
    assert cfg.tenant_id == TENANT_ID
    assert cfg.scan_mode == "b2b_precision"
    assert cfg.target_segments == ("b2b",)
    assert cfg.place_type_whitelist == ("supermarket", "hardware_store")
    assert cfg.place_type_priority == {"supermarket": 10, "hardware_store": 7}
    assert cfg.ateco_whitelist == ("47.11", "47.52")
    assert cfg.min_employees == 10
    assert cfg.max_revenue_eur == 50_000_000
    assert cfg.technical_b2b.min_area_sqm == 500.0
    assert cfg.technical_b2c.min_kwp == 3.0
    assert cfg.scoring_threshold == 70
    assert cfg.monthly_scan_budget_eur == 1500.0
    assert cfg.scan_grid_density_m == 25
    assert cfg.atoka_enabled is True
    assert cfg.wizard_completed_at is not None
    assert cfg.wizard_pending is False


def test_parse_row_handles_trailing_z_in_timestamp() -> None:
    row = _full_row()
    row["wizard_completed_at"] = "2026-04-01T10:30:00Z"
    cfg = _parse_row(row)
    assert cfg.wizard_completed_at is not None
    assert cfg.wizard_completed_at.tzinfo is not None
    # Z must be interpreted as UTC
    assert cfg.wizard_completed_at.utcoffset() == timezone.utc.utcoffset(None)


def test_parse_row_helpers() -> None:
    cfg = _parse_row(_full_row())
    assert cfg.filters_for("b2b") is cfg.technical_b2b
    assert cfg.filters_for("b2c") is cfg.technical_b2c
    assert cfg.targets("b2b") is True
    assert cfg.targets("b2c") is False


# ---------------------------------------------------------------------------
# _parse_row — sparse / null-tolerant
# ---------------------------------------------------------------------------


def test_parse_row_sparse_uses_defaults() -> None:
    row = {
        "tenant_id": str(TENANT_ID),
        "scan_mode": "opportunistic",
        # Every other field absent or None.
        "target_segments": None,
        "place_type_whitelist": None,
        "place_type_priority": None,
        "technical_filters": None,
        "wizard_completed_at": None,
    }
    cfg = _parse_row(row)
    assert cfg.scan_mode == "opportunistic"
    assert cfg.target_segments == ("b2b",)  # fallback
    assert cfg.place_type_whitelist == ("establishment",)
    assert cfg.place_type_priority == {}
    assert cfg.ateco_whitelist == ()
    assert cfg.ateco_priority == {}
    # Technical filters default to permissive (0 / 1.0)
    assert cfg.technical_b2b.max_shading == 1.0
    assert cfg.technical_b2c.min_kwp == 0.0
    # Scoring threshold falls back to 60 when missing
    assert cfg.scoring_threshold == 60
    # Grid density falls back to 30 when missing
    assert cfg.scan_grid_density_m == 30
    assert cfg.atoka_enabled is False
    assert cfg.wizard_completed_at is None
    assert cfg.wizard_pending is True


# ---------------------------------------------------------------------------
# _default_for
# ---------------------------------------------------------------------------


def test_default_for_is_opportunistic_and_wizard_pending() -> None:
    cfg = _default_for(TENANT_ID)
    assert cfg.tenant_id == TENANT_ID
    assert cfg.scan_mode == "opportunistic"
    assert cfg.target_segments == ("b2b", "b2c")
    assert cfg.wizard_pending is True
    assert cfg.atoka_enabled is False
    # Sensible B2B filter defaults (conservative)
    assert cfg.technical_b2b.min_area_sqm == 500
    assert cfg.technical_b2b.min_kwp == 50
    assert cfg.technical_b2c.min_area_sqm == 60
