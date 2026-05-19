/**
 * Pure-function derivations from Google Solar API output.
 *
 * Takes the raw roof + Solar payload we already persist on the lead
 * (no extra API calls) and produces every derived number an operator
 * needs to read or write a quote: install cost estimate, payback,
 * monthly production curve, surface coverage %, battery / inverter
 * sizing, panel layout efficiency.
 *
 * Defaults reflect Italian 2025-2026 PV market pricing for the demo
 * tenant's segment (industrial / commercial 20-50 kWp). The
 * `costAssumptions` parameter lets the operator override per-tenant
 * once we wire that into settings.
 *
 * Why pure-frontend:
 *   - Display-only, no DB writes — recompute on every render
 *   - Same numbers as the preventivo/PDF flow uses elsewhere; if
 *     anything ever drifts, this file is the single source of truth
 *   - No new API trips → SolarApiInspector stays fast
 */

import type { LeadDetailRow } from '@/types/db';

/** Italy monthly yield distribution (% of annual production by month).
 *  PVGIS averages 2010-2020 for typical south-facing 30° tilt array;
 *  the relative shape is what matters here, the absolute kWh comes
 *  from `estimated_yearly_kwh` which Solar API already gives us. */
export const ITALY_MONTHLY_DISTRIBUTION_PCT: readonly number[] = [
  // Jan, Feb, Mar, Apr, May, Jun, Jul, Aug, Sep, Oct, Nov, Dec
  4.5, 5.5, 8.0, 9.5, 11.5, 12.5, 13.5, 12.0, 9.5, 7.0, 4.0, 2.5,
] as const;

export const MONTH_NAMES_IT: readonly string[] = [
  'Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu',
  'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic',
] as const;

/** Italian PV market pricing assumptions (2025-2026, full key-turn,
 *  including inverter, mounting, electrical, labour, certifications).
 *  Tiered by system size — bigger systems get better €/kWp because the
 *  fixed labour + permitting cost amortises across more capacity.
 *  Based on public pricing aggregators (ANIE, GSE quote DB) + known
 *  installer rates for Northern/Central Italy. Adjust per-tenant once
 *  the operator wires their actual installer rates. */
export interface CostAssumptions {
  /** €/kWp installed, full turnkey. */
  costPerKwpEur: number;
  /** Reference grid electricity tariff (€/kWh) used to compute savings.
   *  Industry average for B2B Italy 2025: 0.22-0.30 €/kWh. */
  gridTariffEurPerKwh: number;
  /** Self-consumption ratio — fraction of produced kWh that the
   *  business uses directly. Without battery: 30-50% for residential,
   *  50-70% for B2B with daytime operations. */
  selfConsumptionRatio: number;
  /** Wholesale buy-back price for surplus (Scambio sul Posto / RID).
   *  Lower than tariff because it's the market price, not retail. */
  feedInTariffEurPerKwh: number;
  /** CO₂ kg avoided per kWh produced (Italian grid emission factor
   *  2024, terna.it). */
  co2KgPerKwh: number;
}

export const DEFAULT_COST_ASSUMPTIONS: CostAssumptions = {
  costPerKwpEur: 1500, // industrial 20-50 kWp band
  gridTariffEurPerKwh: 0.27,
  selfConsumptionRatio: 0.6,
  feedInTariffEurPerKwh: 0.08,
  co2KgPerKwh: 0.28,
};

function pickCostPerKwp(estimatedKwp: number): number {
  // Tiered curve — big systems get cheaper per kWp.
  if (estimatedKwp >= 100) return 1200;
  if (estimatedKwp >= 50) return 1350;
  if (estimatedKwp >= 20) return 1500;
  if (estimatedKwp >= 10) return 1700;
  return 2000; // residential / very small
}

export interface Derivations {
  // --- Direct from Solar API ---
  panelCount: number;
  panelCapacityW: number;
  panelAreaSqm: number;
  estimatedKwp: number;
  estimatedYearlyKwh: number;
  roofAreaSqm: number;

  // --- Layout efficiency ---
  /** Fraction of total roof surface covered by panels (0..1). */
  roofCoveragePct: number;
  /** Specific yield kWh / kWp / year — sanity check vs Italian
   *  expectation (1100-1400 healthy, < 900 = shaded / wrong azimuth). */
  specificYieldKwhPerKwp: number;

  // --- Cost & ROI ---
  /** Estimated installation cost in € (turnkey). */
  estimatedInstallCostEur: number;
  /** € saved per year via self-consumption + feed-in. */
  estimatedAnnualSavingsEur: number;
  /** Years to break even on the install cost (no incentives). */
  paybackYears: number;
  /** Tonnes of CO₂ avoided over 25-year system lifetime. */
  co2Tonnes25Years: number;

  // --- Sizing recommendations ---
  /** Recommended inverter rated power (kW). Sized below kWp by ~10%
   *  per AC-side efficiency convention. */
  recommendedInverterKw: number;
  /** Recommended battery capacity (kWh) for self-consumption boost.
   *  Rule of thumb: 1-1.5 × daily average production. */
  recommendedBatteryKwh: number;

  // --- Monthly production ---
  /** kWh produced per month (Jan..Dec). Sums to estimatedYearlyKwh. */
  monthlyProductionKwh: number[];
  /** Monthly self-consumption × tariff savings (Jan..Dec) in €. */
  monthlySavingsEur: number[];
}

/**
 * Best-effort projection of the persisted ``roofs.derivations`` JSONB
 * (computed by ``apps/api/src/services/roi_service.py:compute_full_derivations``)
 * into the frontend ``Derivations`` shape.
 *
 * We use the cached snapshot WHEN present so the dashboard inspector,
 * email body and preventivo PDF show the same numbers — the backend
 * computation is the single source of truth.  The local TS computation
 * is a fallback for legacy roofs (no cached column) and a sanity check
 * during development.
 */
function fromCachedDerivations(
  cached: Record<string, unknown>,
  fallbackRoofArea: number,
): Derivations {
  const num = (k: string, dflt = 0): number =>
    typeof cached[k] === 'number' ? (cached[k] as number) : dflt;
  const arr = (k: string): number[] =>
    Array.isArray(cached[k]) ? (cached[k] as number[]) : [];

  const panelCount = num('panel_count');
  const panelW = num('panel_width_m', 1.05);
  const panelH = num('panel_height_m', 1.95);
  const panelAreaSqm = panelW * panelH;
  const estimatedKwp = num('estimated_kwp');
  const estimatedYearlyKwh = num('yearly_kwh');

  // Prefer the physics-based realistic saving (autoconsumo =
  // min(produzione, fabbisogno)) — the same field the hero KPIs, the
  // outreach email and the dossier now display.
  const realisticSavings = num('realistic_yearly_savings_eur');
  const annualSavings =
    realisticSavings > 0 ? realisticSavings : num('yearly_savings_eur');

  return {
    panelCount,
    panelCapacityW: num('panel_capacity_w', 410),
    panelAreaSqm,
    estimatedKwp,
    estimatedYearlyKwh,
    roofAreaSqm: fallbackRoofArea,
    roofCoveragePct: num('roof_coverage_pct'),
    specificYieldKwhPerKwp: num('specific_yield_kwh_per_kwp'),
    estimatedInstallCostEur: num('gross_capex_eur'),
    estimatedAnnualSavingsEur: annualSavings,
    paybackYears: num('payback_years'),
    co2Tonnes25Years: num('co2_tonnes_25_years'),
    recommendedInverterKw: num('recommended_inverter_kw'),
    recommendedBatteryKwh: num('recommended_battery_kwh'),
    monthlyProductionKwh: arr('monthly_production_kwh'),
    monthlySavingsEur: arr('monthly_savings_eur'),
  };
}

export function deriveSolarMetrics(
  lead: LeadDetailRow,
  assumptions: Partial<CostAssumptions> = {},
): Derivations | null {
  const roof = lead.roofs;
  if (!roof) return null;

  // Fast path: read the cached server-side derivations when present.
  // Bypasses the local TS recomputation and guarantees parity with the
  // email/preventivo output for the same lead. Migrations 0094+
  // populate this column on every roof write.
  const cached = (roof as { derivations?: unknown }).derivations;
  if (
    cached &&
    typeof cached === 'object' &&
    'monthly_production_kwh' in (cached as Record<string, unknown>)
  ) {
    return fromCachedDerivations(
      cached as Record<string, unknown>,
      roof.area_sqm ?? 0,
    );
  }

  // Pull raw Solar API payload — extracted by the same shape
  // SolarApiInspector consumes.
  type SolarPanelEntry = { yearlyEnergyDcKwh?: number };
  type SolarPotential = {
    maxArrayPanelsCount?: number;
    panelCapacityWatts?: number;
    panelHeightMeters?: number;
    panelWidthMeters?: number;
    solarPanels?: SolarPanelEntry[];
  };
  const raw = (roof.raw_data ?? null) as { solarPotential?: SolarPotential } | null;
  const potential = raw?.solarPotential;

  const panelCount =
    potential?.solarPanels?.length ?? potential?.maxArrayPanelsCount ?? 0;
  const panelCapacityW = potential?.panelCapacityWatts ?? 410;
  const panelW = potential?.panelWidthMeters ?? 1.05;
  const panelH = potential?.panelHeightMeters ?? 1.95;
  const panelAreaSqm = panelW * panelH;

  // ── Consistency layer ───────────────────────────────────────────
  // When the lead carries a backend roi_data snapshot, the cost / ROI
  // figures shown here MUST match it — the same numbers the hero KPIs,
  // the outreach email and the dossier read. The local TS recompute
  // below survives only for geometry / sizing / the monthly-curve shape
  // (panel-only, never compared across surfaces) and as a last resort
  // for roi_data-less legacy leads.
  const roi = lead.roi_data ?? null;
  const roiNum = (v: unknown): number | null =>
    typeof v === 'number' && Number.isFinite(v) ? v : null;

  const estimatedKwp =
    roiNum(roi?.estimated_kwp) ??
    roof.estimated_kwp ??
    (panelCount * panelCapacityW) / 1000;
  const estimatedYearlyKwh =
    roiNum(roi?.yearly_kwh) ?? roof.estimated_yearly_kwh ?? estimatedKwp * 1300;
  const roofAreaSqm = roof.area_sqm ?? 0;

  // Coverage: panels × area / total roof area. Cap at 1.
  const panelArrayAreaSqm = panelCount * panelAreaSqm;
  const roofCoveragePct =
    roofAreaSqm > 0 ? Math.min(1, panelArrayAreaSqm / roofAreaSqm) : 0;

  const specificYieldKwhPerKwp =
    estimatedKwp > 0 ? estimatedYearlyKwh / estimatedKwp : 0;

  const a: CostAssumptions = {
    ...DEFAULT_COST_ASSUMPTIONS,
    ...assumptions,
    costPerKwpEur:
      assumptions.costPerKwpEur ?? pickCostPerKwp(estimatedKwp),
  };

  // Local TS recompute — used only when roi_data has no usable value.
  const selfConsumedKwh = estimatedYearlyKwh * a.selfConsumptionRatio;
  const feedInKwh = estimatedYearlyKwh - selfConsumedKwh;
  const localAnnualSavingsEur =
    selfConsumedKwh * a.gridTariffEurPerKwh +
    feedInKwh * a.feedInTariffEurPerKwh;

  // Cost / ROI: prefer the backend roi_data snapshot so this panel
  // agrees with the hero KPIs, the email and the dossier; "Risparmio
  // annuo" prefers the realistic figure exactly like those surfaces.
  const estimatedInstallCostEur =
    roiNum(roi?.gross_capex_eur) ?? estimatedKwp * a.costPerKwpEur;
  const estimatedAnnualSavingsEur =
    roiNum(roi?.realistic_yearly_savings_eur) ??
    roiNum(roi?.yearly_savings_eur) ??
    roiNum(roi?.annual_savings_eur) ??
    localAnnualSavingsEur;
  const paybackYears =
    roiNum(roi?.payback_years) ??
    (estimatedAnnualSavingsEur > 0
      ? estimatedInstallCostEur / estimatedAnnualSavingsEur
      : 0);
  const roiCo2KgPerYear =
    roiNum(roi?.co2_kg_per_year) ?? roiNum(roi?.co2_saved_kg);
  const co2Tonnes25Years =
    roiCo2KgPerYear != null
      ? (roiCo2KgPerYear * 25) / 1000
      : (estimatedYearlyKwh * a.co2KgPerKwh * 25) / 1000;

  // Inverter at 90% of DC kWp (typical PR sizing rule).
  const recommendedInverterKw = Math.round(estimatedKwp * 0.9 * 10) / 10;
  // Battery at ~1.2× daily average — so daytime overflow can power
  // the next morning's load.
  const dailyAvgKwh = estimatedYearlyKwh / 365;
  const recommendedBatteryKwh = Math.round(dailyAvgKwh * 1.2 * 2) / 2;

  // Monthly distribution — use the canonical Italian curve. Monthly
  // savings are scaled so the 12 months sum to the annual figure above,
  // keeping the chart consistent with the headline number.
  const monthlyProductionKwh = ITALY_MONTHLY_DISTRIBUTION_PCT.map(
    (pct) => (estimatedYearlyKwh * pct) / 100,
  );
  const monthlySavingsEur = monthlyProductionKwh.map((kwh) =>
    estimatedYearlyKwh > 0
      ? (kwh / estimatedYearlyKwh) * estimatedAnnualSavingsEur
      : 0,
  );

  return {
    panelCount,
    panelCapacityW,
    panelAreaSqm,
    estimatedKwp,
    estimatedYearlyKwh,
    roofAreaSqm,
    roofCoveragePct,
    specificYieldKwhPerKwp,
    estimatedInstallCostEur,
    estimatedAnnualSavingsEur,
    paybackYears,
    co2Tonnes25Years,
    recommendedInverterKw,
    recommendedBatteryKwh,
    monthlyProductionKwh,
    monthlySavingsEur,
  };
}
