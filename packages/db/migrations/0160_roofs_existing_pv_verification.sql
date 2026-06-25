-- 0160_roofs_existing_pv_verification.sql
--
-- Fail-closed existing-PV gate.
--
-- Problem: `roofs.has_existing_pv` is a plain boolean defaulting to FALSE, set
-- TRUE only when satellite vision CONFIDENTLY detects panels. So `false` is
-- ambiguous: it means EITHER "vision confidently saw no panels" OR "we never
-- ran/never got a confident verdict" (the default). The L4 gate failed OPEN
-- (None / low-confidence / false → keep the lead), so a roof that already has
-- panels but wasn't confidently flagged (e.g. Hotel Olimpico) sailed through to
-- ready_to_send and got pitched solar.
--
-- Fix: record WHEN a confident verdict was obtained, so callers can require a
-- POSITIVE "verified clean" signal before a lead is ever marked ready_to_send
-- or emailed.
--
--   verified clean    = existing_pv_checked_at IS NOT NULL AND has_existing_pv = false
--   verified has-pv   = has_existing_pv = true            (reject / blacklist)
--   UNVERIFIED (hold) = existing_pv_checked_at IS NULL    (vision didn't run, or
--                       confidence below threshold → never confidently decided)
--
-- Additive + nullable: existing roofs keep has_existing_pv=false with
-- existing_pv_checked_at=NULL, i.e. they read as UNVERIFIED until re-checked.
-- No data migration; backfill of the live ready_to_send warehouse is handled
-- separately (held + re-verified) so nothing un-verified keeps sending.

ALTER TABLE roofs
  ADD COLUMN IF NOT EXISTS existing_pv_checked_at timestamptz,
  ADD COLUMN IF NOT EXISTS existing_pv_confidence  real;

COMMENT ON COLUMN roofs.existing_pv_checked_at IS
  'When a CONFIDENT existing-PV verdict (vision confidence >= EXISTING_PV_MIN_CONFIDENCE) was last obtained. NULL = never confidently verified -> treat as UNVERIFIED (hold, do not promote/send). Verified-clean = existing_pv_checked_at IS NOT NULL AND has_existing_pv = false.';

COMMENT ON COLUMN roofs.existing_pv_confidence IS
  'Vision confidence (0..1) of the last existing-PV verdict that set has_existing_pv / existing_pv_checked_at.';
