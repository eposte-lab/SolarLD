"""Unit tests for savings_compare_service.

Pure functions, network-free. Cover the predicted-vs-actual comparison and
the annual EPC framing (``compute_epc_annual``) that feeds both the dossier
panel and the dashboard BollettaCard — the numbers must match.
"""

from __future__ import annotations

from src.services.savings_compare_service import (
    compute_epc_annual,
    compute_savings_compare,
)


def _result(*, predicted_kwh: float, bolletta_kwh: float, bolletta_eur: float):
    roi = {
        "yearly_kwh": predicted_kwh,
        "yearly_savings_eur": 8000,
        "payback_years": 6,
        "net_capex_eur": 48000,
    }
    res = compute_savings_compare(
        roi_data=roi,
        bolletta_kwh_yearly=bolletta_kwh,
        bolletta_eur_yearly=bolletta_eur,
        subject_type="b2b",
    )
    assert res is not None
    return res


def test_epc_annual_need_above_production() -> None:
    """Fabbisogno (60k) > producibilità (100k capped da self-consumo)."""
    res = _result(predicted_kwh=100000, bolletta_kwh=60000, bolletta_eur=18000)
    epc = compute_epc_annual(res)
    # effective = min(100000, 60000) = 60000; tariff = 0.30
    # epc_saving = 60000 * 0.30 * 0.20 = 3600
    assert epc["current_annual_eur"] == 18000
    assert epc["saving_annual_eur"] == 3600
    assert epc["epc_annual_eur"] == 14400
    assert epc["pct_off"] == 20.0
    assert epc["saving_10y_eur"] == 36000


def test_epc_annual_capped_by_production() -> None:
    """Producibilità (30k) < fabbisogno (60k): il tetto limita il 20%."""
    res = _result(predicted_kwh=30000, bolletta_kwh=60000, bolletta_eur=18000)
    epc = compute_epc_annual(res)
    # effective = min(30000, 60000) = 30000; tariff = 0.30
    # epc_saving = 30000 * 0.30 * 0.20 = 1800
    assert epc["saving_annual_eur"] == 1800
    assert epc["epc_annual_eur"] == 16200
    assert epc["pct_off"] == 10.0
    assert epc["saving_10y_eur"] == 18000


def test_savings_compare_none_without_roi() -> None:
    assert (
        compute_savings_compare(
            roi_data=None,
            bolletta_kwh_yearly=60000,
            bolletta_eur_yearly=18000,
            subject_type="b2b",
        )
        is None
    )


def test_savings_compare_none_with_nonpositive_bolletta() -> None:
    roi = {"yearly_kwh": 100000, "yearly_savings_eur": 8000}
    assert (
        compute_savings_compare(
            roi_data=roi,
            bolletta_kwh_yearly=0,
            bolletta_eur_yearly=18000,
            subject_type="b2b",
        )
        is None
    )
