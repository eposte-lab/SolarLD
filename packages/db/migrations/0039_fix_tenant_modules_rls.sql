-- 0039 — Fix RLS policy on tenant_modules.
--
-- The original 0032 policy compared `tenant_id` against `auth.uid()`:
--
--     USING (tenant_id = auth.uid() OR auth.role() = 'service_role')
--
-- This is a type-match accident — `auth.uid()` is the caller's USER
-- uuid, `tenant_id` is a TENANT uuid. They can never match (except in
-- the degenerate case where a tenant's UUID happens to equal a user's
-- UUID, which we never rely on). The upshot: every authenticated read
-- via the anon key (i.e. every dashboard server component using
-- `createSupabaseServerClient`) saw **zero rows**, because the
-- predicate was always false.
--
-- Symptoms in the app:
--   * Onboarding loop: `isOnboardingPending` reads tenant_modules to
--     check row existence. RLS hides every row → "missing" → redirect
--     to `/onboarding`. User finishes wizard, API saves succeed (service
--     role bypasses RLS), `router.push('/')` lands back on the dashboard
--     layout which re-runs the check → loop.
--   * `getModulesForTenant` in `modules.server.ts` silently falls back
--     to synthesised defaults on every read, so the tenant's custom
--     pipeline labels / ATECO codes / kWp thresholds never render in
--     settings UI until a service-role path reloads them.
--
-- Fix: reuse the same `auth_tenant_id()` helper every other tenant-
-- scoped table uses (introduced in 0015). The function is SECURITY
-- DEFINER and reads `tenant_members` with the owner's rights, so we
-- dodge the recursion trap that caused 0015 in the first place.
--
-- Scope: single policy swap. No data migration needed — the rows are
-- already there, they just weren't visible.

BEGIN;

DROP POLICY IF EXISTS tenant_modules_own ON tenant_modules;

CREATE POLICY tenant_modules_own ON tenant_modules
    FOR ALL
    USING (tenant_id = auth_tenant_id() OR auth.role() = 'service_role')
    WITH CHECK (tenant_id = auth_tenant_id() OR auth.role() = 'service_role');

COMMIT;
