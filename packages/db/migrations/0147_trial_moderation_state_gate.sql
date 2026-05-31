-- ============================================================
-- 0147 — Trial moderation: shift the gate from row-visibility to
--        lead-STATE promotion
-- ============================================================
-- Revised requirement (Total Trade owner, pre go-live): the tenant must
-- SEE its own contatti and open the schede/dossier of the IDs it was
-- sent — it only ever saw the bare IDs under "Invii" before, because
-- migration 0145's `leads_select` policy hid every un-released row at the
-- DB level. The operator does NOT want to release/curate the contatti
-- themselves; the only thing under operator control is the STATE change
-- contatto → lead (plus the inbound sopralluogo email, already held in
-- the API). A contatto that reacts (click / portal visit / WhatsApp /
-- reply / appointment) must NOT auto-promote to an active "lead" in the
-- tenant's dashboard — it surfaces in the super-admin first and the
-- operator promotes it.
--
-- So the gate moves UP, out of RLS:
--   • RLS goes back to plain tenant-scoping → the tenant sees all of its
--     own lead rows again (contatti visible, schede openable).
--   • The "is this row a *lead* yet?" gate now lives in the dashboard's
--     lead-surface queries (apps/dashboard/src/lib/data/leads.ts):
--     for a moderated tenant the /leads list, hot-leads widgets and the
--     hot-leads KPI additionally require `operator_released_at IS NOT
--     NULL`. /contatti (scan_candidates) and /invii (outreach_sends) are
--     never gated. `operator_released_at` therefore keeps its column
--     meaning ("operator promoted this to a lead"), only its enforcement
--     point changes.
--
-- This is behaviour-neutral for every NON-moderated tenant: 0145's
-- `leads_select` already collapsed to `tenant_id = auth_tenant_id()` for
-- them (tenant_is_moderated() = false). For the moderated tenant it
-- un-hides the previously-hidden rows — the intended (and only) change.
--
-- Untouched: leads_insert/update/delete (0145), tenant_is_moderated()
-- (still used by the API appointment/moderation services and the
-- pending-leads queue), pending_inbound_requests (default-deny), the
-- write-time event gate for held appointments.
-- ============================================================

-- ------------------------------------------------------------
-- Relax leads SELECT back to plain tenant-scoping.
-- ------------------------------------------------------------
DROP POLICY IF EXISTS leads_select ON leads;

CREATE POLICY leads_select ON leads
  FOR SELECT
  USING (tenant_id = auth_tenant_id());
