-- 0029 — Add `b2b_ateco_precision` to the scan_mode whitelist.
--
-- The legacy `b2b_precision` mode uses Google Places Nearby Search as the
-- discovery engine, with only a lossy place_type_whitelist as input filter.
-- Places does not expose ATECO codes, so it's unable to honor the tenant's
-- ideal customer profile precisely.
--
-- `b2b_ateco_precision` replaces discovery with a direct Atoka v2 search:
--   ateco_codes[] ∧ province ∧ employees_range ∧ revenue_range
--
-- Discovery returns structured HQ addresses which get forward-geocoded and
-- fed into Google Solar exactly like the legacy path. Both modes coexist
-- during migration — tenants with wizard_completed_at prior to this release
-- keep running `b2b_precision` until they re-open the wizard.
--
-- Idempotent: the CHECK relaxation is tolerant of re-runs because we drop
-- and re-add by name.

BEGIN;

ALTER TABLE tenant_configs
    DROP CONSTRAINT IF EXISTS tenant_configs_scan_mode_check;

ALTER TABLE tenant_configs
    ADD CONSTRAINT tenant_configs_scan_mode_check
    CHECK (scan_mode IN (
        'b2b_precision',         -- legacy: Google Places + Solar
        'b2b_ateco_precision',   -- NEW: Atoka discovery by ATECO + Solar
        'opportunistic',
        'volume'
    ));

COMMIT;
