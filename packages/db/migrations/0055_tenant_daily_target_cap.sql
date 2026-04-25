-- ============================================================
-- 0055 — tenant daily target send cap (Sprint 2)
-- ============================================================
-- Hard cap on the number of "in-target" emails a tenant may send
-- per calendar day (Europe/Rome).
--
-- Why this exists:
--   The product's contractual SLA is "~250 in-target emails/day per
--   tenant" — a guarantee that protects sender reputation, GDPR
--   manageability, and prevents a single tenant from torching their
--   own domain in week 1. Until now this was enforced only at the
--   inbox level (per-mailbox 50/day curve) which means a tenant with
--   8+ inboxes could legally fire 400+/day without hitting any cap.
--
--   The new column lives on `tenants` (not on `acquisition_campaigns`)
--   because the limit is *per-tenant*, not per-campaign — multiple
--   active campaigns share the same daily budget.
--
--   Default 250 matches the SLA. Ops can bump enterprise tier to
--   500 by an UPDATE on the column directly; no UI exposure on the
--   tenant side because raising it has compliance implications and
--   should be a deliberate ops action.
--
-- Counter implementation:
--   The actual counter lives in Redis (key
--   ``daily_target_cap:{tenant_id}:{YYYY-MM-DD}`` in Europe/Rome) so
--   reads/writes are O(1) at outreach hot-path. Postgres only stores
--   the cap value — Redis stores the running count, with TTL 36h so
--   stale counters expire on their own.

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS daily_target_send_cap INTEGER NOT NULL DEFAULT 250;

COMMENT ON COLUMN tenants.daily_target_send_cap IS
  'Hard cap on in-target outreach emails per Europe/Rome calendar day. '
  'Default 250 = product SLA. Ops may raise per-tenant for enterprise. '
  'Counter lives in Redis at daily_target_cap:{tenant_id}:{YYYY-MM-DD}.';

-- Sanity check: refuse silly values that would either disable the
-- cap entirely (0 / negative) or push past Gmail's daily-send hard
-- limits (a Workspace inbox tops out at ~2000/day; a *tenant* with
-- multiple inboxes shouldn't exceed 5000 without an explicit ops
-- review of the deliverability stack).
ALTER TABLE tenants
  ADD CONSTRAINT tenants_daily_target_send_cap_range
  CHECK (daily_target_send_cap > 0 AND daily_target_send_cap <= 5000);
