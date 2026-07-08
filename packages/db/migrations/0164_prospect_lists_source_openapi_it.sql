-- 0164 — allow the energivori (OpenAPI VAT) discovery channel as a prospect_list source.
--
-- prospect_lists.source was added inline + UNNAMED in 0106 (Postgres auto-named
-- it prospect_lists_source_check) allowing only 'atoka' | 'places'. The new
-- by-P.IVA channel enriches via OpenAPI.it (company.openapi.com), so add
-- 'openapi_it' as a provenance value. 'source' is a backend/telemetry column
-- (not owner-facing UI), so a vendor-ish token is fine here.
--
-- Live constraint name confirmed in prod via pg_constraint before writing this.

BEGIN;

ALTER TABLE prospect_lists
  DROP CONSTRAINT IF EXISTS prospect_lists_source_check;

ALTER TABLE prospect_lists
  ADD CONSTRAINT prospect_lists_source_check
    CHECK (source IN ('atoka', 'places', 'openapi_it'));

COMMIT;
