-- 0116_creative_skipped_reason.sql
--
-- Adds leads.creative_skipped_reason so the CreativeAgent can persist
-- WHY a render produced only the static after-image (or nothing). Today
-- the reason is logged via structlog and immediately lost; the operator
-- has to grep Railway logs to figure out whether the sidecar was
-- unreachable, the Solar API didn't find the building, ROI inputs were
-- empty, etc. With the column in place the dashboard lead-detail page
-- can surface a small diagnostic chip ("Video non generato — sidecar
-- non raggiungibile") without needing log access.
--
-- Nullable + additive — drop with `ALTER TABLE leads DROP COLUMN
-- creative_skipped_reason` if rollback is required.

ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS creative_skipped_reason TEXT;

COMMENT ON COLUMN leads.creative_skipped_reason IS
  'Last gif_fallback_reason / skipped_reason from CreativeAgent '
  '(remotion_failed, before_url_missing, after_url_missing, roi_missing, '
  'missing_coords, solar_api_key_not_configured, replicate_token_not_configured, '
  'roof_confidence_too_low:*, solar_no_building, ai_paint_error:*, '
  'solar_render_error:*). Surfaces sidecar / data-quality issues to ops '
  'without scraping logs. Last write wins.';
