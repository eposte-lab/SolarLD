-- ============================================================
-- 0015 — Fix RLS recursion on tenant_members / tenants
-- ============================================================
--
-- The 0011 RLS setup had a chicken-and-egg problem:
--
--   1. `auth_tenant_id()` did  SELECT tenant_id FROM tenant_members
--      WHERE user_id = auth.uid().
--   2. The policy `members_select` on tenant_members used
--      tenant_id = auth_tenant_id().
--   3. Evaluating the policy therefore re-queried tenant_members,
--      which is itself RLS-protected. The inner query returned
--      zero rows, auth_tenant_id() returned NULL, the outer
--      predicate became `tenant_id = NULL` = UNKNOWN, and no rows
--      were visible to any authenticated user.
--
--   Same knock-on effect on `tenants_select` (depends on
--   auth_tenant_id() which depends on tenant_members visibility).
--
-- Symptom in the app: `getCurrentTenantContext()` always returned
-- null → the dashboard layout redirected every authenticated user
-- to `/no-tenant` even when their tenant_members row existed.
--
-- Fix:
--   a) Make `auth_tenant_id()` SECURITY DEFINER so the helper's
--      internal SELECT runs with the function owner's privileges
--      and bypasses RLS. Lock down `search_path` to avoid the
--      classic SECURITY DEFINER injection vector.
--   b) Replace the `tenant_members` SELECT policy with the direct
--      rule `user_id = auth.uid()`. Every user can read their own
--      memberships; no helper indirection needed. This is both
--      simpler and more honest about what the policy is expressing.
--   c) Leave `tenants_select` using auth_tenant_id(), which now
--      works because the helper bypasses RLS on its inner lookup.
-- ============================================================

-- Recreate the helper with SECURITY DEFINER semantics.
CREATE OR REPLACE FUNCTION auth_tenant_id()
RETURNS UUID
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT tenant_id
  FROM tenant_members
  WHERE user_id = auth.uid()
  LIMIT 1;
$$;

-- Lock function ownership and permissions.
-- The function must run as a role that can read tenant_members
-- regardless of RLS — in Supabase that's `postgres`.
ALTER FUNCTION auth_tenant_id() OWNER TO postgres;

REVOKE ALL ON FUNCTION auth_tenant_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth_tenant_id() TO authenticated, anon, service_role;

-- Replace the members_select policy with a direct user_id check.
-- Users always see their own memberships; no recursion.
DROP POLICY IF EXISTS members_select ON tenant_members;

CREATE POLICY members_select ON tenant_members
  FOR SELECT
  USING (user_id = auth.uid());
