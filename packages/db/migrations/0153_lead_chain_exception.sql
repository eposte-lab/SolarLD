-- 0153_lead_chain_exception.sql
-- Per-lead override for the national-chain + generic-mailbox send exclusion.
-- A chain HQ generic (info@hilton.com) is normally skipped + blacklisted, but
-- the operator can deliberately KEEP a specific location (e.g. they found a
-- usable contact, or want to try that property) by flagging the lead. The
-- outreach chain guard honours this flag and does not blacklist it.
BEGIN;

ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS chain_exception boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN leads.chain_exception IS
  'Operator override: when true, the national-chain+generic send guard does NOT exclude this lead.';

COMMIT;
