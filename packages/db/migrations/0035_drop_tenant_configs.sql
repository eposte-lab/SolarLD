-- 0035 — Retire v1 Hunter config schema.
--
-- The v2 cut-over completed: every Hunter config now lives in
-- `tenant_modules` (migration 0032), scan modes are restricted to
-- `b2b_funnel_v2` + `b2c_residential` (migration 0030), and no Python
-- or TypeScript reader touches `tenant_configs` / `ateco_google_types`
-- anymore. Time to drop the tables so a new install doesn't seed
-- rows nobody reads.
--
-- This migration is deliberately one-way: there is no rollback to v1.
-- Tenants created after 0035 land directly in the modular world; the
-- few installed tenants have already been backfilled by the
-- `tenant_modules` INSERT in 0032.
--
-- Dropped artifacts:
--
--   tenant_configs              — monolithic v1 config row
--     ├─ tenant_configs_scan_mode_check    (dropped implicitly via table)
--     └─ idx_tenant_configs_scan_mode      (dropped implicitly via table)
--
--   ateco_google_types          — wizard dropdown seed (v1 only)
--
-- CASCADE is required because 0013 created an FK from `scan_runs` to
-- nothing — but other auxiliary views or logging helpers may still
-- reference `tenant_configs`. Any object that does will be dropped
-- alongside; if that breaks a forgotten caller, the app startup
-- ImportError is cleaner than silent data drift.

BEGIN;

DROP TABLE IF EXISTS tenant_configs CASCADE;
DROP TABLE IF EXISTS ateco_google_types CASCADE;

COMMIT;
