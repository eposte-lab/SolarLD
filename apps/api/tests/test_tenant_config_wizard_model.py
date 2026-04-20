"""Unit tests for the wizard endpoint's pydantic validation surface.

We don't spin up a full FastAPI client here — the route module's
business logic all lives in `tenant_config_service` (already tested)
so these tests focus on the *input validation*: bounds, literals,
deduping, default fallbacks. Validation bugs here would let bad data
reach the DAO, so keeping them pure and fast is valuable.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.routes.tenant_config import WizardIn


def _base_payload() -> dict:
    return {
        "scan_mode": "b2b_precision",
        "target_segments": ["b2b"],
        "ateco_codes": ["47.11"],
    }


def test_minimal_payload_applies_defaults() -> None:
    w = WizardIn(**_base_payload())
    assert w.scan_mode == "b2b_precision"
    assert w.target_segments == ["b2b"]
    assert w.ateco_codes == ["47.11"]
    # Defaults
    assert w.max_shading == 0.5
    assert w.min_exposure_score == 0.6
    assert w.scan_priority_zones == ["capoluoghi"]
    assert w.monthly_scan_budget_eur == 1500.0
    assert w.monthly_outreach_budget_eur == 2000.0
    assert w.scoring_threshold == 60
    assert w.min_kwp_b2b is None
    assert w.min_kwp_b2c is None


def test_invalid_scan_mode_rejected() -> None:
    payload = _base_payload()
    payload["scan_mode"] = "carpet_bombing"
    with pytest.raises(ValidationError):
        WizardIn(**payload)


def test_invalid_segment_rejected() -> None:
    payload = _base_payload()
    payload["target_segments"] = ["gov"]
    with pytest.raises(ValidationError):
        WizardIn(**payload)


def test_empty_segments_rejected() -> None:
    payload = _base_payload()
    payload["target_segments"] = []
    with pytest.raises(ValidationError):
        WizardIn(**payload)


def test_shading_out_of_range_rejected() -> None:
    payload = _base_payload()
    payload["max_shading"] = 1.5
    with pytest.raises(ValidationError):
        WizardIn(**payload)


def test_negative_budget_rejected() -> None:
    payload = _base_payload()
    payload["monthly_scan_budget_eur"] = -10
    with pytest.raises(ValidationError):
        WizardIn(**payload)


def test_scoring_threshold_above_100_rejected() -> None:
    payload = _base_payload()
    payload["scoring_threshold"] = 120
    with pytest.raises(ValidationError):
        WizardIn(**payload)


def test_min_kwp_b2b_negative_rejected() -> None:
    payload = _base_payload()
    payload["min_kwp_b2b"] = -5
    with pytest.raises(ValidationError):
        WizardIn(**payload)


def test_ateco_codes_deduped_in_order() -> None:
    payload = _base_payload()
    payload["ateco_codes"] = ["47.11", "47.52", "47.11", "56.10", "47.52"]
    w = WizardIn(**payload)
    assert w.ateco_codes == ["47.11", "47.52", "56.10"]


def test_b2b_precision_empty_codes_accepted_at_model_layer() -> None:
    """The 422 for b2b_precision + empty codes is enforced in the
    route handler, not the pydantic model — so the model itself must
    accept the shape (covered by route-level guard).
    """
    payload = _base_payload()
    payload["ateco_codes"] = []
    w = WizardIn(**payload)
    assert w.ateco_codes == []


def test_opportunistic_mode_minimal_ok() -> None:
    payload = {
        "scan_mode": "opportunistic",
        "target_segments": ["b2b", "b2c"],
    }
    w = WizardIn(**payload)
    assert w.scan_mode == "opportunistic"
    assert w.ateco_codes == []
    assert w.target_segments == ["b2b", "b2c"]


def test_volume_mode_accepted() -> None:
    payload = {
        "scan_mode": "volume",
        "target_segments": ["b2c"],
    }
    w = WizardIn(**payload)
    assert w.scan_mode == "volume"
    assert w.target_segments == ["b2c"]
