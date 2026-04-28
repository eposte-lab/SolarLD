"""Savings compare — predicted vs actual based on uploaded bolletta.

Sprint 8 Fase B.3.

The bolletta gives us *ground truth* on the lead's current consumption
(kWh/yr) and current spend (€/yr). Combine that with the SolarLD ROI
estimate (built from the satellite-derived rooftop) and we get a
side-by-side panel that says:

  * "Stima SolarLD" — what we predicted from the rooftop alone
  * "La tua bolletta reale" — what you actually pay today
  * Delta — how much of a gap (or surplus) exists between prediction
    and reality, and the *new* payback computed against the actual
    tariff (€/kWh) the lead is currently paying

The recomputed savings use the lead's *actual* €/kWh tariff (eur/kwh)
applied to the self-consumed share of the rooftop's yearly_kwh.
That's the number the salesperson can quote with confidence — it
isn't an industry average, it's *this* lead's bill.

Pure functions, no DB/HTTP. The /v1/public/lead/{slug}/savings-compare
endpoint loads ROI + latest bolletta from Supabase and feeds them in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Mirrors roi_service constants — we re-derive savings against the
# actual tariff but keep the same self-consumption split so the two
# panels are apples-to-apples comparable.
SELF_CONSUMPTION_RATIO_B2C = 0.40
SELF_CONSUMPTION_RATIO_B2B = 0.65
EXPORT_PRICE_EUR_PER_KWH = 0.09


@dataclass(frozen=True, slots=True)
class SavingsCompareResult:
    """Side-by-side comparison surfaced by SavingsComparePanel."""

    # Predicted (from satellite ROI estimate)
    predicted_yearly_kwh: float
    predicted_yearly_savings_eur: float
    predicted_payback_years: float | None

    # Actual (from uploaded bolletta)
    actual_yearly_kwh: float
    actual_yearly_eur: float
    actual_tariff_eur_per_kwh: float

    # Re-computed savings, using the lead's actual tariff applied to
    # the rooftop's predicted yearly production (same self-consumption
    # split as the ROI service).
    actual_yearly_savings_eur: float
    actual_payback_years: float | None
    actual_self_consumption_kwh: float
    actual_export_kwh: float

    # Delta vs predicted: positive ``delta_pct`` means the actual
    # savings BEAT the prediction (= the lead is currently paying
    # MORE than industry-average tariff, the rooftop pays back
    # faster than the standard estimate). The UI flips colour:
    # amber for "you are over-paying" (good pitch), mint for
    # "in line with prediction".
    delta_savings_eur: float
    delta_pct: float

    def to_jsonb(self) -> dict[str, float | None]:
        """Wire format for the public endpoint."""
        return {
            "predicted_yearly_kwh": round(self.predicted_yearly_kwh, 0),
            "predicted_yearly_savings_eur": round(
                self.predicted_yearly_savings_eur, 0
            ),
            "predicted_payback_years": (
                round(self.predicted_payback_years, 1)
                if self.predicted_payback_years is not None
                else None
            ),
            "actual_yearly_kwh": round(self.actual_yearly_kwh, 0),
            "actual_yearly_eur": round(self.actual_yearly_eur, 0),
            "actual_tariff_eur_per_kwh": round(
                self.actual_tariff_eur_per_kwh, 4
            ),
            "actual_yearly_savings_eur": round(
                self.actual_yearly_savings_eur, 0
            ),
            "actual_payback_years": (
                round(self.actual_payback_years, 1)
                if self.actual_payback_years is not None
                else None
            ),
            "actual_self_consumption_kwh": round(
                self.actual_self_consumption_kwh, 0
            ),
            "actual_export_kwh": round(self.actual_export_kwh, 0),
            "delta_savings_eur": round(self.delta_savings_eur, 0),
            "delta_pct": round(self.delta_pct, 1),
        }


def compute_savings_compare(
    *,
    roi_data: dict[str, Any] | None,
    bolletta_kwh_yearly: float,
    bolletta_eur_yearly: float,
    subject_type: str,
    net_capex_eur: float | None = None,
) -> SavingsCompareResult | None:
    """Build the predicted-vs-actual comparison.

    Returns ``None`` when:
      * ``roi_data`` is missing the predicted savings/yearly_kwh, or
      * the bolletta values are non-positive.

    The caller (route) is responsible for fetching ``roi_data`` from
    ``leads.roi_data`` and the latest manual/OCR values from
    ``bolletta_uploads`` (manual values take precedence over OCR
    values when both are present).
    """
    if not roi_data:
        return None
    if bolletta_kwh_yearly <= 0 or bolletta_eur_yearly <= 0:
        return None

    predicted_yearly_kwh = _to_float(
        roi_data.get("yearly_kwh") or roi_data.get("estimated_yearly_kwh")
    )
    predicted_yearly_savings = _to_float(roi_data.get("yearly_savings_eur"))
    if predicted_yearly_kwh is None or predicted_yearly_savings is None:
        return None

    predicted_payback = _to_float(roi_data.get("payback_years"))
    if net_capex_eur is None:
        net_capex_eur = _to_float(roi_data.get("net_capex_eur"))

    # Actual €/kWh from the bolletta is the killer datum: it's typically
    # 0.08-0.12 above the national average for Italian residential users
    # paying spot+oneri+iva, and that's exactly the gap the rooftop
    # closes most.
    tariff = bolletta_eur_yearly / bolletta_kwh_yearly

    st = (subject_type or "unknown").lower()
    if st == "b2b":
        self_ratio = SELF_CONSUMPTION_RATIO_B2B
    else:
        self_ratio = SELF_CONSUMPTION_RATIO_B2C

    self_kwh = predicted_yearly_kwh * self_ratio
    export_kwh = predicted_yearly_kwh * (1.0 - self_ratio)
    actual_savings = (
        self_kwh * tariff + export_kwh * EXPORT_PRICE_EUR_PER_KWH
    )

    actual_payback: float | None = None
    if net_capex_eur and net_capex_eur > 0 and actual_savings > 0:
        actual_payback = net_capex_eur / actual_savings

    delta = actual_savings - predicted_yearly_savings
    delta_pct = (
        (delta / predicted_yearly_savings) * 100.0
        if predicted_yearly_savings > 0
        else 0.0
    )

    return SavingsCompareResult(
        predicted_yearly_kwh=predicted_yearly_kwh,
        predicted_yearly_savings_eur=predicted_yearly_savings,
        predicted_payback_years=predicted_payback,
        actual_yearly_kwh=bolletta_kwh_yearly,
        actual_yearly_eur=bolletta_eur_yearly,
        actual_tariff_eur_per_kwh=tariff,
        actual_yearly_savings_eur=actual_savings,
        actual_payback_years=actual_payback,
        actual_self_consumption_kwh=self_kwh,
        actual_export_kwh=export_kwh,
        delta_savings_eur=delta,
        delta_pct=delta_pct,
    )


def _to_float(val: object) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN check
        return None
    return f
