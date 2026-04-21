-- ============================================================
-- 0037 — add 'whatsapp' to outreach_channel enum
-- ============================================================
-- Postgres enums are append-only without a full column rebuild.
-- ADD VALUE is safe in a running cluster: the new value is
-- available immediately for new rows; existing rows that store
-- 'email' or 'postal' are unaffected.
--
-- The Python OutreachChannel enum is kept in sync in
-- apps/api/src/models/enums.py.

ALTER TYPE outreach_channel ADD VALUE IF NOT EXISTS 'whatsapp';
