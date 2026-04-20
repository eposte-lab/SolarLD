-- ============================================================
-- 0028 — Index on campaigns.postal_tracking_number
-- ============================================================
-- Motivation:
--   The Pixart inbound webhook (Fase 1 finalization plan) resolves
--   a webhook event to a campaign by the tracking number that Pixart
--   assigns when a postcard is printed. Without this index the lookup
--   would be a sequential scan on campaigns — trivial today, expensive
--   once volume grows.
--
--   Column `postal_tracking_number` already exists (see 0007_campaigns.sql:22).
--   `postal_provider_order_id` already has an index at 0007:44 — this is
--   the symmetric index for the tracking-number lookup path.
--
-- Partial on IS NOT NULL: the overwhelming majority of campaigns are
--   email-channel with no postal tracking number, so the partial index
--   stays small.

CREATE INDEX IF NOT EXISTS idx_campaigns_postal_tracking
  ON campaigns(postal_tracking_number)
  WHERE postal_tracking_number IS NOT NULL;
