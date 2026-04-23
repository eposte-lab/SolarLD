-- ============================================================
-- 0043 — Rename campaigns → outreach_sends
-- ============================================================
--
-- "campaigns" historically meant individual send records (one row = one
-- email / postal / WA message). Phase A of the Grande Rename:
--
--   OLD: campaigns       (individual send records)
--   NEW: outreach_sends  (same data, clearer name)
--
-- The concept of an acquisition campaign (ATECO target + geo + budget)
-- is introduced in migration 0044 as a new `acquisition_campaigns` table.
--
-- Renamed in this migration:
--   - Table:         campaigns          → outreach_sends
--   - Indexes:       idx_campaigns_*    → idx_outreach_sends_*
--   - Trigger:       trg_campaigns_*    → trg_outreach_sends_*
--   - RLS policy:    campaigns_all      → outreach_sends_all
--   - FK column:     tenant_inboxes.campaigns.inbox_id is fine
--     (the FK on tenant_inboxes references the old table name in the
--      migration 0042 DDL — after this rename the FK is now on
--      outreach_sends.inbox_id; Postgres resolves it at statement time
--      so the FK name in the catalog changes; the column stays.)
--
-- Backward compatibility: no data is touched. All existing rows land in
-- outreach_sends verbatim.

BEGIN;

-- ── 1. Rename the table itself ────────────────────────────────────────
ALTER TABLE campaigns RENAME TO outreach_sends;

-- ── 2. Rename indexes ─────────────────────────────────────────────────
ALTER INDEX idx_campaigns_lead
    RENAME TO idx_outreach_sends_lead;

ALTER INDEX idx_campaigns_scheduled
    RENAME TO idx_outreach_sends_scheduled;

ALTER INDEX idx_campaigns_tenant_status
    RENAME TO idx_outreach_sends_tenant_status;

ALTER INDEX idx_campaigns_email_msg
    RENAME TO idx_outreach_sends_email_msg;

ALTER INDEX idx_campaigns_postal_order
    RENAME TO idx_outreach_sends_postal_order;

-- ── 3. Rename trigger ─────────────────────────────────────────────────
ALTER TRIGGER trg_campaigns_updated_at
    ON outreach_sends
    RENAME TO trg_outreach_sends_updated_at;

-- ── 4. Rename RLS policy ──────────────────────────────────────────────
ALTER POLICY campaigns_all ON outreach_sends
    RENAME TO outreach_sends_all;

COMMIT;
