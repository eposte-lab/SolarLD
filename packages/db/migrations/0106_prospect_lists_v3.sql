-- 0106_prospect_lists_v3.sql
--
-- Bridge "Trova aziende" /scoperta to the v3 funnel:
--
--   1. prospect_list_items now stores Google Places metadata (no more
--      Atoka-only payload) and tracks per-item validation status.
--   2. prospect_lists tracks the lifecycle of an on-demand validation +
--      outreach run (started_at / completed_at timestamps).
--
-- Workflow after this migration:
--   /scoperta search → POST /v1/prospector/lists (Places-based items
--     inserted with validation_status='pending')
--   → operator clicks "Convalida per fotovoltaico" → ARQ task fans out
--     L2-L5 per item, sets validation_status to accepted/rejected/no_building
--   → operator clicks "Lancia outreach" → ARQ task promotes accepted items
--     to subjects+leads, queues outreach_task for each (daily cap respected
--     by the existing outreach pipeline).

-- 1) Drop NOT NULL on vat_number — Places-based rows never have a P.IVA
--    until the L2 scraping extracts it. legal_name remains NOT NULL: we
--    use Places `display_name` to populate it.
ALTER TABLE prospect_list_items
  ALTER COLUMN vat_number DROP NOT NULL;

-- 2) Places-specific columns
ALTER TABLE prospect_list_items
  ADD COLUMN IF NOT EXISTS google_place_id VARCHAR(200),
  ADD COLUMN IF NOT EXISTS place_lat NUMERIC(10,7),
  ADD COLUMN IF NOT EXISTS place_lng NUMERIC(10,7),
  ADD COLUMN IF NOT EXISTS place_types TEXT[],
  ADD COLUMN IF NOT EXISTS business_status VARCHAR(40),
  ADD COLUMN IF NOT EXISTS user_ratings_total INT,
  ADD COLUMN IF NOT EXISTS rating NUMERIC(3,1),
  ADD COLUMN IF NOT EXISTS phone TEXT,
  ADD COLUMN IF NOT EXISTS google_maps_uri TEXT;

-- 3) Validation tracking
ALTER TABLE prospect_list_items
  ADD COLUMN IF NOT EXISTS validation_status VARCHAR(20)
    DEFAULT 'pending'
    CHECK (validation_status IN (
      'pending',
      'validating',
      'accepted',
      'rejected',
      'no_building',
      'api_error',
      'skipped'
    )),
  ADD COLUMN IF NOT EXISTS validated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS scan_candidate_id UUID REFERENCES scan_candidates(id) ON DELETE SET NULL;

-- 4) Dedup index on Places ID (one item per list per place)
CREATE UNIQUE INDEX IF NOT EXISTS idx_pli_list_place
  ON prospect_list_items(list_id, google_place_id)
  WHERE google_place_id IS NOT NULL;

-- 5) Validation status filter index
CREATE INDEX IF NOT EXISTS idx_pli_list_status
  ON prospect_list_items(list_id, validation_status);

-- 6) List-level lifecycle bookkeeping
ALTER TABLE prospect_lists
  ADD COLUMN IF NOT EXISTS validation_started_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS validation_completed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS outreach_started_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS outreach_completed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'atoka'
    CHECK (source IN ('atoka', 'places'));

-- 7) Mark all existing rows as 'atoka' (legacy). New rows from /scoperta v3
--    will set source='places' explicitly.
UPDATE prospect_lists SET source = 'atoka' WHERE source IS NULL;
