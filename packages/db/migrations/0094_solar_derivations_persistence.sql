-- 0094_solar_derivations_persistence.sql
--
-- Persist the full set of solar derivations (cost estimate, sizing,
-- monthly production curve, coverage %, specific yield, ROI) on the
-- ``roofs`` row, and let tenants override the underlying cost
-- assumptions (€/kWp tier, grid tariff, self-consumption ratio,
-- feed-in tariff, CO₂ factor, incentive %).
--
-- Why persist on roofs:
--   * Single source of truth — the dashboard inspector, the email
--     body, the preventivo PDF, and the GSE practice flow all read
--     from the same JSONB instead of recomputing locally and risking
--     drift between channels.
--   * Snapshot at quote-send time — when the operator sends the
--     outreach email at T0, the numbers in the email match what's
--     persisted; even if the assumptions change at T1, the
--     historical record stays consistent for that lead.
--   * Saves render cost — frontend reads an already-computed dict
--     instead of doing the math on every page render.
--
-- Why per-tenant assumptions:
--   * Different installers have different €/kWp tiers (the demo
--     defaults are public-market averages; Soluzioni Solari Bergamo
--     might quote 1450 €/kWp where the demo tenant uses 1500).
--   * Grid tariff varies by region + tenant agreement.
--   * Self-consumption ratio depends on the tenant's typical
--     customer profile (B2B logistics vs B2C residential).

-- 1. Per-tenant cost-assumption overrides. NULL means "use defaults
--    from roi_service.py module constants" — tenants don't have to
--    configure anything to get a working estimate.
ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS cost_assumptions JSONB;

COMMENT ON COLUMN tenants.cost_assumptions IS
  'Optional per-tenant overrides for the ROI calculator. Shape: {"capex_eur_per_kwp_b2c": 1500, "capex_eur_per_kwp_b2b": 1200, "grid_price_eur_per_kwh_b2c": 0.25, "grid_price_eur_per_kwh_b2b": 0.22, "self_consumption_ratio_b2c": 0.4, "self_consumption_ratio_b2b": 0.65, "export_price_eur_per_kwh": 0.09, "co2_kg_per_kwh": 0.281, "incentive_pct_b2c": 0.5, "incentive_pct_b2b": 0.3, "incentive_pct_fallback": 0.1}. Any subset of keys is allowed; missing keys fall through to roi_service module defaults. NULL = use defaults entirely.';

-- 2. Cached full derivations on the roof row. Computed by
--    roi_service.compute_full_derivations() at roof creation time
--    (level4_solar_gate, demo route, creative.py snap) and updated
--    on every recompute (e.g. operator clicks "Rigenera rendering").
ALTER TABLE roofs
  ADD COLUMN IF NOT EXISTS derivations JSONB;

COMMENT ON COLUMN roofs.derivations IS
  'Cached full derivation dict from roi_service.compute_full_derivations(): cost / ROI / sizing recommendations / monthly production curve / coverage / specific yield. Single source of truth for the dashboard inspector, email templates, preventivo PDF. NULL = not yet computed (legacy roof or cascade still running) — readers should fall back to the lighter-weight roi_data on leads.';

CREATE INDEX IF NOT EXISTS roofs_derivations_idx
  ON roofs USING gin (derivations);
