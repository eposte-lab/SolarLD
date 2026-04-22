-- Migration 0041 — RLS + dashboard grants for scan_candidates + scan_cost_log
--
-- scan_candidates was created in 0031 without RLS — it was only accessed by
-- the API service-role client (bypasses RLS). Now that the dashboard also
-- reads it directly (Supabase SSR with user JWT), we need:
--   1. RLS enabled + tenant-scoped SELECT policy
--   2. SELECT grant to the `authenticated` role
--
-- scan_cost_log has the same gap. Add the same treatment.

BEGIN;

-- ---- scan_candidates ----

ALTER TABLE scan_candidates ENABLE ROW LEVEL SECURITY;

-- Allow the authenticated Supabase user to read only their tenant's rows.
-- auth_tenant_id() is defined in 0011 and returns the tenant_id bound to
-- the current JWT via tenant_members.
CREATE POLICY sc_tenant_iso ON scan_candidates
    FOR ALL
    USING (tenant_id = auth_tenant_id());

-- Dashboard SSR client runs as the authenticated role.
GRANT SELECT ON scan_candidates TO authenticated;

-- The API service-role client bypasses RLS so the Python agents still work.
-- No change needed there; GRANT is for the dashboard JWT path.

-- ---- scan_cost_log ----

ALTER TABLE scan_cost_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY scl_tenant_iso ON scan_cost_log
    FOR ALL
    USING (tenant_id = auth_tenant_id());

GRANT SELECT ON scan_cost_log TO authenticated;

COMMIT;
