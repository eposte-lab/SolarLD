-- 0109_proxy_score_quality_flags.sql
--
-- Index to filter candidates by the anti-spam validator's
-- `recommended_for_rendering` flag without scanning the whole JSONB.
-- The flag is persisted by L5 proxy_score after the
-- `lead_quality_validator` post-processing step (Sprint maggio 2026).

CREATE INDEX IF NOT EXISTS idx_scan_candidates_quality_hard_reject
  ON scan_candidates ((proxy_score_data->>'recommended_for_rendering'))
  WHERE proxy_score_data IS NOT NULL;
