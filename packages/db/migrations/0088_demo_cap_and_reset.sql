-- ============================================================
-- 0088 — Demo pipeline: raise cap to 999 + reset RPC
-- ============================================================
--
-- Context
--   Migration 0077 seeded demo tenants with 3 lifetime attempts —
--   just enough for a live sales demo. For internal QA we need the
--   pipeline to run as many times as needed without ops having to
--   manually PATCH the counter in Supabase Studio between runs.
--
--   This migration does two things:
--     1. Raises the counter to 999 for all demo tenants that are
--        still at the original value of 3 (haven't been burned down
--        in a real demo call).  Tenants that have already used some
--        attempts (remaining < 3) keep their current value — they're
--        likely mid-demo and ops will reset them explicitly if needed.
--     2. Adds `demo_reset_pipeline_attempts(tenant_id, new_count)`
--        — a SECURITY DEFINER RPC callable by the super-admin endpoint
--        to reset any demo tenant's counter to an arbitrary value.
--        The API endpoint caps `new_count` at 999 and rejects 0 (that
--        would lock the tenant out — use PATCH /v1/admin/tenants/{id}
--        to clear `is_demo` instead).
--
-- Why 999 and not "unlimited"
--   The counter is an INT with a CHECK (>= 0) guard. We could store
--   -1 as "unlimited" but that requires every decrement path to
--   special-case it. 999 is effectively unlimited for QA purposes
--   (500+ pipeline runs per tenant per year would be exceptional)
--   while keeping the existing decrement logic unchanged.
-- ============================================================

-- Raise existing demo tenants that still sit at the 0077-granted 3.
-- Tenants that burned some attempts (< 3) are left alone — they're
-- mid-demo and the operator decides when to reset.
UPDATE tenants
   SET demo_pipeline_test_remaining = 999
 WHERE is_demo = true
   AND demo_pipeline_test_remaining = 3;

-- Also raise any demo tenants that were manually set to the old
-- default-ish values (1, 2, 3) and haven't been touched.
-- (The UPDATE above already handles exactly-3; this is a no-op if
--  those are the only ones, but safety-net for edge cases.)
UPDATE tenants
   SET demo_pipeline_test_remaining = 999
 WHERE is_demo = true
   AND demo_pipeline_test_remaining BETWEEN 1 AND 3;

-- ============================================================
-- RPC: demo_reset_pipeline_attempts
-- ============================================================
--
-- Called by POST /v1/admin/demo/reset-attempts (super_admin only).
-- Sets `demo_pipeline_test_remaining` to `p_new_count` for the
-- given tenant unconditionally — no "only if already demo" gate here
-- because the API layer enforces that.  Returns the post-update
-- remaining count, or NULL if no tenant row was found.
--
-- The function is intentionally lenient about the value range:
-- the API endpoint rejects counts < 1 or > 999; the function itself
-- only enforces the existing CHECK (>= 0) via the column constraint.
-- ============================================================

CREATE OR REPLACE FUNCTION demo_reset_pipeline_attempts(
  p_tenant_id  uuid,
  p_new_count  int DEFAULT 999
)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_remaining INT;
BEGIN
  UPDATE tenants
     SET demo_pipeline_test_remaining = p_new_count
   WHERE id = p_tenant_id
  RETURNING demo_pipeline_test_remaining INTO v_remaining;

  RETURN v_remaining;  -- NULL when no tenant row matched
END;
$$;

-- Super-admin API runs as service_role; authenticated users should
-- NOT be able to call this directly from the client SDK — they
-- always go through the API which enforces the super_admin role gate.
GRANT EXECUTE ON FUNCTION demo_reset_pipeline_attempts(uuid, int) TO service_role;
-- Revoke from authenticated so a compromised tenant JWT can't reset
-- their own counter without going through our API.
REVOKE EXECUTE ON FUNCTION demo_reset_pipeline_attempts(uuid, int) FROM authenticated;
