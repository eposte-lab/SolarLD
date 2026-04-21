-- 0030 — Add `b2b_funnel_v2` + `b2c_residential` to scan_mode whitelist.
--
-- The v2 rewrite replaces the single-stage `b2b_ateco_precision` with a
-- proper 4-level funnel (Atoka discovery → Maps enrichment → AI proxy
-- scoring → Solar gate on top 10-20% only). Solar is no longer called on
-- every Atoka candidate — it becomes the final premium filter.
--
--   LEVEL 1: Atoka v2 search (ateco × province × size × revenue)
--   LEVEL 2: Places Details + website heuristics (no Solar yet)
--   LEVEL 3: Claude Haiku proxy score 0-100 (no Solar yet)
--   LEVEL 4: Solar findClosest on score ≥ P80 only → final lead
--
-- `b2c_residential` is the parallel residential pipeline: ISTAT income
-- dataset per CAP → audience segments → letter/Meta Ads/door-to-door
-- outreach, with Solar reversed to post-engagement only.
--
-- Backward compatibility: `b2b_ateco_precision` is kept temporarily as a
-- deprecated alias during the rollover (tenants wizard-migrated to
-- `b2b_funnel_v2` on next reopen). Legacy `b2b_precision` stays active for
-- pre-v2 tenants.
--
-- Idempotent: drop+re-add constraint by name.

BEGIN;

ALTER TABLE tenant_configs
    DROP CONSTRAINT IF EXISTS tenant_configs_scan_mode_check;

ALTER TABLE tenant_configs
    ADD CONSTRAINT tenant_configs_scan_mode_check
    CHECK (scan_mode IN (
        'b2b_precision',         -- legacy: Google Places + Solar
        'b2b_ateco_precision',   -- deprecated alias of b2b_funnel_v2
        'b2b_funnel_v2',         -- NEW: 4-level funnel (Atoka→Enrich→Score→Solar)
        'b2c_residential',       -- NEW: residential ISTAT-income audiences
        'opportunistic',
        'volume'
    ));

COMMIT;
