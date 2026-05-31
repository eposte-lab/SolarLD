-- ============================================================
-- 0145 — Trial moderation gate (super-admin curation layer)
-- ============================================================
-- Per-tenant moderation gate for supervised trials (first user:
-- "Total Trade"). When a tenant is *moderated*, the operator
-- (super_admin) curates what the tenant perceives:
--
--   * leads are HIDDEN from the tenant (RLS) until the operator
--     explicitly "releases" them — outreach to prospects keeps
--     running normally; only the tenant's *visibility* is gated;
--   * inbound prospect requests (the dossier appointment form) are
--     held in `pending_inbound_requests` and routed to the operator
--     first, reaching the tenant only after approval (handled in the
--     API, see routes/public.py + routes/admin.py).
--
-- This migration is BEHAVIOR-NEUTRAL on deploy: no tenant has the
-- `trial_moderation` flag set, so `tenant_is_moderated()` returns
-- false everywhere and the new leads RLS collapses to the old rule.
-- The flag is flipped for Total Trade in a separate migration (0146)
-- only after this one is verified in production.
--
-- The moderation flag lives in tenants.settings.feature_flags
-- (JSONB), toggled by the existing PATCH /v1/admin/tenants/{id}/
-- feature-flags endpoint — no schema change needed to enable/disable.
-- ============================================================

-- ------------------------------------------------------------
-- 1) Per-lead visibility gate columns
-- ------------------------------------------------------------
-- operator_released_at IS NULL + review_status='pending'  → hidden
--   from a moderated tenant (default for every new lead).
-- operator_released_at = <ts> + review_status='released'   → visible.
-- review_status='held'                                     → explicitly
--   suppressed by the operator (distinct from "not yet reviewed");
--   still hidden. Lets the queue UI tell "pending" from "decided: hide".
-- Non-moderated tenants ignore these columns entirely (see RLS below),
-- so no backfill is required and existing tenants are unaffected.
ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS operator_released_at TIMESTAMPTZ NULL,
  ADD COLUMN IF NOT EXISTS operator_review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (operator_review_status IN ('pending', 'released', 'held'));

-- Partial index keeps the moderated-tenant "released only" filter cheap.
CREATE INDEX IF NOT EXISTS idx_leads_tenant_released
  ON leads(tenant_id) WHERE operator_released_at IS NOT NULL;

-- ------------------------------------------------------------
-- 2) Helper: is this tenant under trial moderation?
-- ------------------------------------------------------------
-- Mirrors auth_tenant_id() (0015): SECURITY DEFINER so the inner
-- read of `tenants` bypasses RLS, STABLE so the planner caches it
-- per-statement, locked search_path against the SECURITY DEFINER
-- injection vector. COALESCE(...,false) = fail-safe-visible: any
-- tenant whose flag is absent behaves exactly as today.
CREATE OR REPLACE FUNCTION tenant_is_moderated(p_tenant_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT COALESCE(
    (SELECT (settings -> 'feature_flags' ->> 'trial_moderation') = 'true'
       FROM tenants WHERE id = p_tenant_id),
    false);
$$;

ALTER FUNCTION tenant_is_moderated(UUID) OWNER TO postgres;
REVOKE ALL ON FUNCTION tenant_is_moderated(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION tenant_is_moderated(UUID) TO authenticated, anon, service_role;

-- ------------------------------------------------------------
-- 3) Rewrite the leads RLS policy — gate SELECT only
-- ------------------------------------------------------------
-- ⚠️ Permissive policies are OR-combined. The previous single
-- `leads_all FOR ALL` policy also covers SELECT; a FOR ALL write
-- policy would re-open SELECT and defeat the gate. So we split into
-- one SELECT policy (moderation-aware) + three write policies
-- (tenant-scoped, moderation-agnostic) — the same pattern used for
-- crm_webhook_subscriptions in 0017.
--
-- For a NON-moderated tenant, tenant_is_moderated() is false ⇒
-- `NOT tenant_is_moderated(...)` is true ⇒ the SELECT USING collapses
-- to `tenant_id = auth_tenant_id()`, byte-for-byte the old behavior.
DROP POLICY IF EXISTS leads_all ON leads;

CREATE POLICY leads_select ON leads
  FOR SELECT
  USING (
    tenant_id = auth_tenant_id()
    AND (
      operator_released_at IS NOT NULL
      OR NOT tenant_is_moderated(tenant_id)
    )
  );

CREATE POLICY leads_insert ON leads
  FOR INSERT
  WITH CHECK (tenant_id = auth_tenant_id());

CREATE POLICY leads_update ON leads
  FOR UPDATE
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

CREATE POLICY leads_delete ON leads
  FOR DELETE
  USING (tenant_id = auth_tenant_id());

-- NOTE: roofs/subjects are intentionally NOT gated. The dashboard only
-- reaches them through embedded joins on `leads`, so a hidden lead's
-- roof/subject never surface via the UI. The `events` policy is also
-- left untouched (append-only, partitioned): the held inbound request
-- is gated at write-time in the API (the appointment event is not
-- emitted for a moderated tenant until approval).

-- ------------------------------------------------------------
-- 4) Held inbound requests queue (operator-only)
-- ------------------------------------------------------------
-- One row per prospect appointment-form submission for a moderated
-- tenant. Read/written only by the API service-role (routes/admin.py);
-- the tenant must NEVER see it. RLS is enabled with NO policy →
-- default-deny for every authenticated/anon role; service_role bypasses
-- RLS. This is the correct "invisible to the tenant" posture.
CREATE TABLE IF NOT EXISTS pending_inbound_requests (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id      UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  payload      JSONB NOT NULL DEFAULT '{}'::jsonb,   -- AppointmentRequest fields
  dossier_url  TEXT,
  status       TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending', 'approved', 'rejected')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at   TIMESTAMPTZ,
  decided_by   UUID                                   -- super_admin user_id
);

CREATE INDEX IF NOT EXISTS idx_pir_status
  ON pending_inbound_requests(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pir_tenant
  ON pending_inbound_requests(tenant_id, created_at DESC);

ALTER TABLE pending_inbound_requests ENABLE ROW LEVEL SECURITY;
-- (intentionally no policy → default-deny; only service_role reads it)
