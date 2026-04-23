"""ROI calculator — pure functions, no DB/HTTP.

The Creative Agent feeds these numbers into the lead landing page and
the outreach email templates. They are deliberately simple and
conservative — the customer sees this as an **indicative** estimate, a
formal `preventivo` happens after the installer is in contact.

Italian market assumptions (2026 Q2 calibration — update yearly):

    CAPEX_EUR_PER_KWP           = 1_500    # turnkey residential
    CAPEX_EUR_PER_KWP_B2B       = 1_200    # commercial scale discount
    GRID_PRICE_EUR_PER_KWH      = 0.25     # PUN + costs average
    GRID_PRICE_EUR_PER_KWH_B2B  = 0.22     # tariffa business media
    SELF_CONSUMPTION_RATIO      = 0.40     # residential without battery
    SELF_CONSUMPTION_RATIO_BIZ  = 0.65     # business has daytime load
    EXPORT_PRICE_EUR_PER_KWH    = 0.09     # RID / Scambio sul Posto 2026
    CO2_KG_PER_KWH              = 0.281    # Terna 2023 mix

Incentives: rather than summing every regional bando we use a flat
percentage haircut off the gross capex that correlates with known
programmes:

    * Superbonus 65% (residenziale B2C) → 50% of CAPEX
    * Credito d'imposta 4.0 (B2B)       → 30% of CAPEX
    * Fallback                          → 10% of CAPEX (conto energia esiste
                                           comunque per lo scambio)
"""

from __future__ import annotations

from dataclasses import dataclass

# ---- Market constants (2026 Q2 calibration) ---------------------------

CAPEX_EUR_PER_KWP_B2C = 1500.0
CAPEX_EUR_PER_KWP_B2B = 1200.0
GRID_PRICE_EUR_PER_KWH_B2C = 0.25
GRID_PRICE_EUR_PER_KWH_B2B = 0.22
SELF_CONSUMPTION_RATIO_B2C = 0.40
SELF_CONSUMPTION_RATIO_B2B = 0.65
EXPORT_PRICE_EUR_PER_KWH = 0.09
CO2_KG_PER_KWH = 0.281

INCENTIVE_PCT_B2C = 0.50
INCENTIVE_PCT_B2B = 0.30
INCENTIVE_PCT_FALLBACK = 0.10


@dataclass(frozen=True, slots=True)
class RoiEstimate:
    """Lead-facing ROI numbers. All monetary values in EUR."""

    estimated_kwp: float
    yearly_kwh: float
    gross_capex_eur: float
    incentive_eur: float
    net_capex_eur: float
    yearly_savings_eur: float
    payback_years: float | None
    co2_kg_per_year: float
    co2_tonnes_25_years: float
    self_consumption_ratio: float
    # True when payback_years ≤ the tenant's roi_target_years.  Surfaces on
    # the lead portal as a green "rientra nel tuo target ROI" badge and lets
    # the creative agent personalise the email copy.
    meets_roi_target: bool = True

    def to_jsonb(self) -> dict[str, float | None | bool]:
        """Shape the estimate for the `leads.roi_data` JSONB column."""
        return {
            "estimated_kwp": round(self.estimated_kwp, 2),
            "yearly_kwh": round(self.yearly_kwh, 0),
            "gross_capex_eur": round(self.gross_capex_eur, 0),
            "incentive_eur": round(self.incentive_eur, 0),
            "net_capex_eur": round(self.net_capex_eur, 0),
            "yearly_savings_eur": round(self.yearly_savings_eur, 0),
            "payback_years": (
                round(self.payback_years, 1) if self.payback_years is not None else None
            ),
            "co2_kg_per_year": round(self.co2_kg_per_year, 0),
            "co2_tonnes_25_years": round(self.co2_tonnes_25_years, 1),
            "self_consumption_ratio": round(self.self_consumption_ratio, 2),
            "meets_roi_target": self.meets_roi_target,
        }


def compute_roi(
    *,
    estimated_kwp: float | None,
    estimated_yearly_kwh: float | None,
    subject_type: str,
    roi_target_years: int | None = None,
) -> RoiEstimate | None:
    """Compute lead-facing ROI.

    Returns ``None`` when inputs are too sparse to produce a credible
    estimate (no kWp *and* no yearly kWh). The caller should skip the
    ROI block in that case rather than showing made-up numbers.
    """
    kwp = _to_float(estimated_kwp)
    yearly_kwh = _to_float(estimated_yearly_kwh)

    if (not kwp or kwp <= 0) and (not yearly_kwh or yearly_kwh <= 0):
        return None

    # Derive the missing side if we have one but not the other.
    # 1300 kWh/kWp is the long-run Italian yield average.
    if kwp is None or kwp <= 0:
        kwp = yearly_kwh / 1300.0
    if yearly_kwh is None or yearly_kwh <= 0:
        yearly_kwh = kwp * 1300.0

    st = (subject_type or "unknown").lower()
    if st == "b2b":
        capex_unit = CAPEX_EUR_PER_KWP_B2B
        grid_price = GRID_PRICE_EUR_PER_KWH_B2B
        self_ratio = SELF_CONSUMPTION_RATIO_B2B
        incentive_pct = INCENTIVE_PCT_B2B
    elif st == "b2c":
        capex_unit = CAPEX_EUR_PER_KWP_B2C
        grid_price = GRID_PRICE_EUR_PER_KWH_B2C
        self_ratio = SELF_CONSUMPTION_RATIO_B2C
        incentive_pct = INCENTIVE_PCT_B2C
    else:
        # Unknown subject → conservative residential defaults.
        capex_unit = CAPEX_EUR_PER_KWP_B2C
        grid_price = GRID_PRICE_EUR_PER_KWH_B2C
        self_ratio = SELF_CONSUMPTION_RATIO_B2C
        incentive_pct = INCENTIVE_PCT_FALLBACK

    gross_capex = kwp * capex_unit
    incentive = gross_capex * incentive_pct
    net_capex = max(0.0, gross_capex - incentive)

    # Savings = self-consumed kWh avoid buying grid; exported kWh sold at
    # the RID price.
    self_kwh = yearly_kwh * self_ratio
    export_kwh = yearly_kwh * (1.0 - self_ratio)
    yearly_savings = self_kwh * grid_price + export_kwh * EXPORT_PRICE_EUR_PER_KWH

    payback: float | None = None
    if yearly_savings > 0:
        payback = net_capex / yearly_savings

    co2_per_year = yearly_kwh * CO2_KG_PER_KWH

    # meets_roi_target: True when payback ≤ tenant's target, or when no
    # target is set (default to True so the badge doesn't show as red
    # for tenants that haven't configured the economico module yet).
    if payback is None or roi_target_years is None or roi_target_years <= 0:
        meets_target = True
    else:
        meets_target = payback <= roi_target_years

    return RoiEstimate(
        estimated_kwp=kwp,
        yearly_kwh=yearly_kwh,
        gross_capex_eur=gross_capex,
        incentive_eur=incentive,
        net_capex_eur=net_capex,
        yearly_savings_eur=yearly_savings,
        payback_years=payback,
        co2_kg_per_year=co2_per_year,
        co2_tonnes_25_years=co2_per_year * 25 / 1000.0,
        self_consumption_ratio=self_ratio,
        meets_roi_target=meets_target,
    )


def _to_float(val: object) -> float | None:
    if val is None:
        return None
    try:
        return float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
