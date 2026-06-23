-- 0159_render_retry.sql
-- Self-healing render recovery: auto-retry transient render failures.
--
-- When a creative render fails for a TRANSIENT reason (solar_render_error /
-- remotion_error / ai_paint_error / *_not_configured / render_unexpected) the
-- lead sits in the warehouse with rendering_image_url NULL forever — nothing
-- re-attempted it. render_retry_cron (every 10 min) now re-enqueues the render
-- with exponential backoff, so e.g. fixing an expired Google Solar key makes
-- the stuck leads re-render on their own within minutes.
--
-- These columns are the cron's bookkeeping, kept SEPARATE from the manual
-- rendering_regen_count so an operator "Rigenera" never eats the auto budget.
BEGIN;

ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS render_retry_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS render_retry_at TIMESTAMPTZ;

COMMENT ON COLUMN leads.render_retry_count IS
  'Number of automatic render retries attempted by render_retry_cron (capped). Separate from manual rendering_regen_count.';
COMMENT ON COLUMN leads.render_retry_at IS
  'Timestamp of the last automatic render retry — drives the exponential backoff in render_retry_service.';

-- Partial index for the cron scan: leads that attempted a render and have no
-- image yet are the only candidates.
CREATE INDEX IF NOT EXISTS idx_leads_render_retry
  ON leads (render_retry_at)
  WHERE rendering_image_url IS NULL AND creative_skipped_reason IS NOT NULL;

COMMIT;
