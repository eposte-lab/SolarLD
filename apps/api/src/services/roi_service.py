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
    # ---- Decision-maker-friendly metrics (the "7 efficientamento" set) ---
    # `yearly_savings_eur` includes the export-to-grid term (RID price),
    # which inflates the headline number. `net_self_savings_eur` follows
    # the strict formula `self_kwh × grid_price` — only what's actually
    # avoided on the bill — so the email block doesn't oversell.
    net_self_savings_eur: float = 0.0
    # 25-year cumulative net savings, with a 0.85 derate for panel
    # degradation. The formula assumes year-1 savings × 25 × 0.85 (a
    # conservative compound average; long-run linear degradation is ~0.5%/yr,
    # so 0.85 is roughly the 25-year average factor).
    savings_25y_eur: float = 0.0
    # ROI a 25 anni recomputed against the strict-self savings — keeps the
    # email block self-consistent (savings_25y / net_capex).
    roi_pct_25y: float = 0.0
    # Avg Italian beech absorbs ~21 kg CO2/year; round the ratio to a clean
    # integer so the email shows e.g. "= 25 alberi piantati".
    trees_equivalent: int = 0
    # True when payback_years ≤ the tenant's roi_target_years.  Surfaces on
    # the lead portal as a green "rientra nel tuo target ROI" badge and lets
    # the creative agent personalise the email copy.
    meets_roi_target: bool = True

    def to_jsonb(self) -> dict[str, float | int | None | bool]:
        """Shape the estimate for the `leads.roi_data` JSONB column."""
        return {
            "estimated_kwp": round(self.estimated_kwp, 2),
            "yearly_kwh": round(self.yearly_kwh, 0),
            "gross_capex_eur": round(self.gross_capex_eur, 0),
            "incentive_eur": round(self.incentive_eur, 0),
            "net_capex_eur": round(self.net_capex_eur, 0),
            "yearly_savings_eur": round(self.yearly_savings_eur, 0),
            "net_self_savings_eur": round(self.net_self_savings_eur, 0),
            "savings_25y_eur": round(self.savings_25y_eur, 0),
            "roi_pct_25y": round(self.roi_pct_25y, 0),
            "trees_equivalent": int(self.trees_equivalent),
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

    # Strict self-consumption savings: only what's avoided on the bill,
    # without the export-to-grid term. This is the conservative number
    # we surface to the decision maker in the email — easier to defend
    # ("we just avoid buying these kWh") and immune to RID-price drift.
    net_self_savings = self_kwh * grid_price
    # 25-year cumulative with degradation derate. The 0.85 factor is the
    # long-run average accounting for ~0.5%/yr panel degradation (so year 25
    # produces ~88% of year 1; mean across the 25 years is ~0.85).
    savings_25y = net_self_savings * 25.0 * 0.85
    # ROI on the strict-self number. If net_capex is zero (full incentive),
    # report 0 to avoid div-by-zero and a meaningless infinity.
    roi_pct_25y = ((savings_25y - net_capex) / net_capex * 100.0) if net_capex > 0 else 0.0

    payback: float | None = None
    if yearly_savings > 0:
        payback = net_capex / yearly_savings

    co2_per_year = yearly_kwh * CO2_KG_PER_KWH
    # Average mature beech absorbs ~21 kg CO2/year (FAO/ISPRA estimate).
    trees_equivalent = round(co2_per_year / 21.0)

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
        net_self_savings_eur=net_self_savings,
        savings_25y_eur=savings_25y,
        roi_pct_25y=roi_pct_25y,
        trees_equivalent=trees_equivalent,
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


# ---------------------------------------------------------------------------
# Full derivations — extends the lightweight `RoiEstimate` (which the
# email body uses) with sizing recommendations, monthly production,
# coverage %, specific yield, panel-array geometry. Persisted to the
# ``roofs.derivations`` JSONB column at roof creation time so the
# dashboard inspector, preventivo PDF, GSE flow all read the same
# numbers without recomputing locally.
# ---------------------------------------------------------------------------


# Italy monthly yield distribution (% of annual production by month).
# PVGIS averages 2010-2020 for typical south-facing 30° tilt array.
# The relative shape is what matters; the absolute kWh comes from the
# yearly_kWh estimate Solar API gives us. Mirrors the equivalent
# constant in apps/dashboard/src/lib/solar-derivations.ts.
ITALY_MONTHLY_DISTRIBUTION_PCT: tuple[float, ...] = (
    4.5, 5.5, 8.0, 9.5, 11.5, 12.5, 13.5, 12.0, 9.5, 7.0, 4.0, 2.5,
)


def _resolve_assumptions(
    tenant_cost_assumptions: dict | None,
    subject_type: str,
) -> dict[str, float]:
    """Merge per-tenant overrides on top of the module defaults.

    The tenant JSONB column can hold any subset of override keys; any
    missing key falls through to the global default below. We resolve
    everything to a flat float dict so the caller doesn't need to
    differentiate between B2B/B2C — that selection happens here.
    """
    overrides = tenant_cost_assumptions or {}
    st = (subject_type or "unknown").lower()

    if st == "b2b":
        capex_unit = overrides.get("capex_eur_per_kwp_b2b", CAPEX_EUR_PER_KWP_B2B)
        grid_price = overrides.get(
            "grid_price_eur_per_kwh_b2b", GRID_PRICE_EUR_PER_KWH_B2B
        )
        self_ratio = overrides.get(
            "self_consumption_ratio_b2b", SELF_CONSUMPTION_RATIO_B2B
        )
        incentive_pct = overrides.get("incentive_pct_b2b", INCENTIVE_PCT_B2B)
    elif st == "b2c":
        capex_unit = overrides.get("capex_eur_per_kwp_b2c", CAPEX_EUR_PER_KWP_B2C)
        grid_price = overrides.get(
            "grid_price_eur_per_kwh_b2c", GRID_PRICE_EUR_PER_KWH_B2C
        )
        self_ratio = overrides.get(
            "self_consumption_ratio_b2c", SELF_CONSUMPTION_RATIO_B2C
        )
        incentive_pct = overrides.get("incentive_pct_b2c", INCENTIVE_PCT_B2C)
    else:
        capex_unit = overrides.get("capex_eur_per_kwp_b2c", CAPEX_EUR_PER_KWP_B2C)
        grid_price = overrides.get(
            "grid_price_eur_per_kwh_b2c", GRID_PRICE_EUR_PER_KWH_B2C
        )
        self_ratio = overrides.get(
            "self_consumption_ratio_b2c", SELF_CONSUMPTION_RATIO_B2C
        )
        incentive_pct = overrides.get(
            "incentive_pct_fallback", INCENTIVE_PCT_FALLBACK
        )

    return {
        "capex_unit": float(capex_unit),
        "grid_price": float(grid_price),
        "self_ratio": float(self_ratio),
        "incentive_pct": float(incentive_pct),
        "export_price": float(
            overrides.get("export_price_eur_per_kwh", EXPORT_PRICE_EUR_PER_KWH)
        ),
        "co2_kg_per_kwh": float(
            overrides.get("co2_kg_per_kwh", CO2_KG_PER_KWH)
        ),
    }


def compute_full_derivations(
    *,
    estimated_kwp: float | None,
    estimated_yearly_kwh: float | None,
    roof_area_sqm: float | None,
    panel_count: int | None,
    panel_capacity_w: float | None = None,
    panel_width_m: float | None = None,
    panel_height_m: float | None = None,
    subject_type: str = "unknown",
    tenant_cost_assumptions: dict | None = None,
    roi_target_years: int | None = None,
) -> dict | None:
    """Compute the full derivation dict for the roofs.derivations column.

    Includes everything `compute_roi` returns (cost / ROI / 25y) plus:
      * sizing: recommended_inverter_kw, recommended_battery_kwh
      * geometry: panel_array_area_sqm, roof_coverage_pct,
        specific_yield_kwh_per_kwp
      * monthly_production_kwh: list[12] of monthly kWh
      * monthly_savings_eur: list[12] of monthly € savings
      * assumptions_resolved: the flat dict actually used (so
        downstream consumers know exactly what the numbers were
        computed against; useful when tenant overrides change later
        and we want to compare snapshot vs current).

    Returns None when the inputs are too sparse for a credible estimate
    (no kWp AND no kWh) — callers should leave the column null.
    """
    base = compute_roi(
        estimated_kwp=estimated_kwp,
        estimated_yearly_kwh=estimated_yearly_kwh,
        subject_type=subject_type,
        roi_target_years=roi_target_years,
    )
    if base is None:
        return None

    a = _resolve_assumptions(tenant_cost_assumptions, subject_type)

    # Geometry / sizing
    pw = panel_width_m or 1.05
    ph = panel_height_m or 1.95
    panel_area = pw * ph
    pc = int(panel_count) if panel_count is not None else 0
    panel_array_area = pc * panel_area if pc > 0 else 0.0
    roof_coverage = (
        min(1.0, panel_array_area / float(roof_area_sqm))
        if roof_area_sqm and roof_area_sqm > 0
        else 0.0
    )
    specific_yield = (
        base.yearly_kwh / base.estimated_kwp if base.estimated_kwp > 0 else 0.0
    )

    # Inverter at 90% of DC kWp; round to one decimal.
    recommended_inverter_kw = round(base.estimated_kwp * 0.9 * 10) / 10
    # Battery at ~1.2× daily average; round to nearest 0.5 kWh.
    daily_avg_kwh = base.yearly_kwh / 365.0
    recommended_battery_kwh = round(daily_avg_kwh * 1.2 * 2) / 2

    # Monthly distribution
    monthly_production = [
        base.yearly_kwh * pct / 100.0 for pct in ITALY_MONTHLY_DISTRIBUTION_PCT
    ]
    monthly_savings = [
        kwh * a["self_ratio"] * a["grid_price"]
        + kwh * (1.0 - a["self_ratio"]) * a["export_price"]
        for kwh in monthly_production
    ]

    return {
        # Lightweight ROI block — same shape compute_roi.to_jsonb()
        # produces, so consumers used to leads.roi_data find the same
        # keys here.
        **base.to_jsonb(),
        # Sizing
        "recommended_inverter_kw": recommended_inverter_kw,
        "recommended_battery_kwh": recommended_battery_kwh,
        # Geometry
        "panel_count": pc,
        "panel_capacity_w": float(panel_capacity_w) if panel_capacity_w else None,
        "panel_width_m": pw,
        "panel_height_m": ph,
        "panel_array_area_sqm": round(panel_array_area, 1),
        "roof_coverage_pct": round(roof_coverage, 3),
        "specific_yield_kwh_per_kwp": round(specific_yield, 0),
        # Monthly curve
        "monthly_production_kwh": [round(x, 0) for x in monthly_production],
        "monthly_savings_eur": [round(x, 0) for x in monthly_savings],
        # Snapshot of the assumptions actually used. Lets the dashboard
        # inspector show "Calcoli basati su €1500/kWp · 0.27€/kWh" so
        # the operator knows whether the numbers reflect their
        # tenant-specific overrides or the public-market defaults.
        "assumptions_resolved": a,
    }
