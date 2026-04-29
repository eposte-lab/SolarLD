-- ============================================================
-- 0075 — Tenant demo flag
-- ============================================================
--
-- Purpose
--   Mark specific tenants as "demo" so the dashboard can hide
--   internal/admin surfaces (Settings hub, pipeline jargon banner)
--   and gate customer-facing features (test pipeline counter).
--
-- Notes
--   Defaults to false: regular customer/installer tenants are
--   unaffected. Only the demo workspace flips this on. The flag
--   is read by the dashboard layout, the territories page, and
--   the Settings nested layout to switch UI to a customer-safe
--   variant.
-- ============================================================

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS is_demo BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_tenants_is_demo
  ON tenants (is_demo)
  WHERE is_demo = true;
