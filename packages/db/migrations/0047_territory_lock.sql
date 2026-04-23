-- ============================================================
-- 0047 — territory lock (contractual territorial exclusivity)
-- ============================================================
--
-- Once onboarding completes, the installer's territorial footprint
-- (regioni / province / CAP + the `territories` rows themselves) is
-- frozen. This is the narrative anchor of the contract: "tu hai
-- Campania esclusiva". The user cannot loosen or widen the zone on
-- their own — only ops can, via POST /v1/admin/tenants/:id/territory-unlock.
--
-- Enforcement layers:
--   1. `tenants.territory_locked_at` — the authoritative flag. Set by
--      POST /v1/onboarding/territory-confirm at the end of the wizard.
--   2. RLS on `territories` — SPLIT from the monolithic `territories_all`
--      policy into per-verb policies, so INSERT/UPDATE/DELETE can
--      additionally require the tenant NOT locked. SELECT stays open.
--   3. API-level checks (apps/api/src/routes/territories.py,
--      modules.py) — return 423 Locked on tenant-scoped writes for
--      nicer error copy than a bare RLS silent failure.
--
-- The service-role client (used by the FastAPI backend) bypasses RLS
-- entirely — ops workflows and migrations continue to work untouched.
-- Enforcement at the API-route layer is the only guard against a
-- logged-in user reaching the service-role path accidentally.

-- ------------------------------------------------------------
-- 1. Add columns
-- ------------------------------------------------------------
ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS territory_locked_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS territory_locked_by UUID REFERENCES auth.users(id);

COMMENT ON COLUMN tenants.territory_locked_at IS
  'When the installer confirmed their territorial exclusivity during onboarding. '
  'NULL = tenant can still edit territories / sorgente geo fields. '
  'NOT NULL = frozen; only service-role (ops) can mutate via admin endpoint.';

COMMENT ON COLUMN tenants.territory_locked_by IS
  'auth.users.id of the user who clicked the confirm button (installer), '
  'OR the ops super_admin who applied the lock manually.';

-- ------------------------------------------------------------
-- 2. Helper predicate for RLS
-- ------------------------------------------------------------
-- STABLE so Postgres can cache it per-statement; SECURITY DEFINER is
-- NOT needed here because tenant_members RLS already gates the calling
-- user's own tenant lookup, and we only look at that tenant.
CREATE OR REPLACE FUNCTION is_tenant_territory_locked(p_tenant_id UUID)
RETURNS BOOLEAN
LANGUAGE sql STABLE
AS $$
  SELECT COALESCE(
    (SELECT territory_locked_at IS NOT NULL FROM tenants WHERE id = p_tenant_id),
    false
  );
$$;

COMMENT ON FUNCTION is_tenant_territory_locked(UUID) IS
  'True when tenant has confirmed territorial exclusivity and can no longer '
  'edit territories or sorgente geo fields via user-role (RLS) writes.';

-- ------------------------------------------------------------
-- 3. Replace `territories_all` with per-verb policies
-- ------------------------------------------------------------
-- The old policy blanket-allowed all verbs when tenant matched. We now:
--   - SELECT — unchanged (tenant scope only)
--   - INSERT — tenant scope AND not locked
--   - UPDATE — tenant scope AND not locked
--   - DELETE — tenant scope AND not locked
DROP POLICY IF EXISTS territories_all ON territories;

CREATE POLICY territories_select ON territories
  FOR SELECT
  USING (tenant_id = auth_tenant_id());

CREATE POLICY territories_insert ON territories
  FOR INSERT
  WITH CHECK (
    tenant_id = auth_tenant_id()
    AND NOT is_tenant_territory_locked(tenant_id)
  );

CREATE POLICY territories_update ON territories
  FOR UPDATE
  USING (
    tenant_id = auth_tenant_id()
    AND NOT is_tenant_territory_locked(tenant_id)
  )
  WITH CHECK (
    tenant_id = auth_tenant_id()
    AND NOT is_tenant_territory_locked(tenant_id)
  );

CREATE POLICY territories_delete ON territories
  FOR DELETE
  USING (
    tenant_id = auth_tenant_id()
    AND NOT is_tenant_territory_locked(tenant_id)
  );

-- ------------------------------------------------------------
-- 4. (No change to tenant_modules RLS)
-- ------------------------------------------------------------
-- Geo fields inside `sorgente.config` (regioni/province/cap) are
-- locked at the *application* layer (apps/api/src/routes/modules.py)
-- rather than via RLS — RLS cannot introspect JSONB structure to
-- compare only three keys without a trigger. The API-level guard in
-- modules.py rejects a PUT that would alter the three keys while
-- keeping non-geo fields editable (ATECO, employees, revenue, B2C
-- income bands).
