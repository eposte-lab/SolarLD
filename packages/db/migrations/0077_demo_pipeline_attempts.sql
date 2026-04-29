-- ============================================================
-- 0077 — Customer-facing "Avvia test pipeline" attempt counter
-- ============================================================
--
-- Purpose
--   The demo tenant exposes a banner in `/leads` that lets the
--   prospect run the full discovery → scoring → outreach pipeline
--   on a company they pick. We cap usage at 3 lifetime attempts
--   (one-shot, never resets) so a curious user can't burn through
--   our Solar / Mapbox / Atoka quotas during a 30-minute call.
--
-- Why a per-tenant integer (and not a row-per-attempt log)
--   We do NOT need to remember which attempts happened or when —
--   only "how many remain". A simple int decremented atomically
--   in the API endpoint is the smallest possible mechanism. The
--   forensic audit (who ran what, with what input) is already
--   covered by the existing `events` table partition that the
--   pipeline writes to anyway. If we ever need a richer audit
--   trail we'll add it then; YAGNI for now.
--
-- Default 0 (feature off)
--   Production tenants don't see the banner — the dashboard
--   conditionally renders it only when `is_demo = true` AND
--   `demo_pipeline_test_remaining > 0`. So even if a non-demo
--   tenant got a non-zero counter by mistake, the UI gate
--   prevents accidental exposure. Defence in depth.
--
-- Atomicity
--   Decrements happen in a single statement:
--     UPDATE tenants SET demo_pipeline_test_remaining =
--       demo_pipeline_test_remaining - 1
--     WHERE id = $1 AND demo_pipeline_test_remaining > 0
--     RETURNING demo_pipeline_test_remaining;
--   No row returned ⇒ counter was already 0 ⇒ API returns 429.
--   The CHECK constraint (>=0) is a safety net so a buggy SQL
--   path can never produce negative remaining attempts.
-- ============================================================

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS demo_pipeline_test_remaining INT NOT NULL DEFAULT 0
  CHECK (demo_pipeline_test_remaining >= 0);

-- Grant 3 attempts to all currently-flagged demo tenants. Idempotent
-- via the WHERE clause: once granted (>0) we don't reset on re-run.
UPDATE tenants
   SET demo_pipeline_test_remaining = 3
 WHERE is_demo = true
   AND demo_pipeline_test_remaining = 0;

-- ============================================================
-- Atomic decrement RPC
-- ============================================================
--
-- Why an RPC and not a plain UPDATE … RETURNING from the API
--   The API runs Python via supabase-py; a single UPDATE with a
--   conditional WHERE is fine for the success path, but we also
--   want the "did this transition succeed?" signal as a single
--   round-trip with no application-side bookkeeping. A SECURITY
--   DEFINER function with a single statement is the cleanest way:
--
--     remaining = demo_decrement_pipeline_attempts(:tenant_id)
--
--   `remaining IS NULL` ⇔ the counter was already 0 ⇒ caller 429s.
--
-- Race-safety
--   The UPDATE on a single row holds a row-level lock; concurrent
--   callers serialise. The CHECK constraint above stops the counter
--   from ever going negative even if two transactions try to
--   decrement at once — the second waits, then sees 0 and returns
--   NULL.
-- ============================================================

CREATE OR REPLACE FUNCTION demo_decrement_pipeline_attempts(p_tenant_id uuid)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_remaining INT;
BEGIN
  UPDATE tenants
     SET demo_pipeline_test_remaining = demo_pipeline_test_remaining - 1
   WHERE id = p_tenant_id
     AND is_demo = true
     AND demo_pipeline_test_remaining > 0
  RETURNING demo_pipeline_test_remaining INTO v_remaining;

  RETURN v_remaining;  -- NULL when no row was updated (already at 0 or non-demo)
END;
$$;

-- Service role and authenticated users can invoke it; the function
-- itself enforces the demo + counter gate so even an authed call
-- from a non-demo tenant safely returns NULL without changing data.
GRANT EXECUTE ON FUNCTION demo_decrement_pipeline_attempts(uuid) TO authenticated, service_role;
