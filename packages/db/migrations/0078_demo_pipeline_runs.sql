-- Demo pipeline run tracking.
--
-- The demo "Avvia test pipeline" endpoint (apps/api/src/routes/demo.py)
-- returns 202 to the browser as soon as scoring is done, then runs
-- creative + outreach in a fire-and-forget asyncio task. Until now
-- failures in that background tail were only logged on the server —
-- the dashboard happily showed a "Lead creato!" toast even when the
-- email never went out. That made the demo look like it works when
-- the recipient inbox had nothing in it.
--
-- This table gives us a per-run state machine the browser can poll:
--
--   scoring  → creative  → outreach  → done
--                    ↓ on exception
--                  failed (with error_message)
--
-- The dashboard dialog polls GET /v1/demo/pipeline-runs/{id} every 2s
-- for ~2 minutes and only flips to a success toast when status='done'.
-- On 'failed' we surface the error and refund the attempt.

CREATE TABLE IF NOT EXISTS demo_pipeline_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  -- Populated as soon as scoring produces a lead row, so the dashboard
  -- can deep-link straight to /leads/{lead_id} on success.
  lead_id uuid REFERENCES leads(id) ON DELETE SET NULL,
  status text NOT NULL CHECK (
    status IN ('scoring', 'creative', 'outreach', 'done', 'failed')
  ),
  -- Step that was active when status flipped to 'failed'. Useful for
  -- triage without parsing error_message.
  failed_step text CHECK (
    failed_step IS NULL OR failed_step IN ('scoring', 'creative', 'outreach')
  ),
  error_message text,
  -- Free-form annotations the pipeline can leave for the user, e.g.
  --   "GIF non generata: after_url_missing"
  -- Stored as text (single string) — we don't need a structured log here,
  -- we want a human-readable note to surface in the dialog.
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Tenant-scoped lookup for the polling endpoint and for the
-- "show me failures from my demo runs" debug query in /admin.
CREATE INDEX IF NOT EXISTS idx_demo_pipeline_runs_tenant_created
  ON demo_pipeline_runs (tenant_id, created_at DESC);

-- Per-lead lookup so the lead detail page can show "this lead was
-- created via demo test pipeline run X" when relevant.
CREATE INDEX IF NOT EXISTS idx_demo_pipeline_runs_lead
  ON demo_pipeline_runs (lead_id) WHERE lead_id IS NOT NULL;

-- Auto-bump updated_at on every UPDATE so polling can show "last
-- updated 3s ago" without an explicit SET.
CREATE OR REPLACE FUNCTION demo_pipeline_runs_touch_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS demo_pipeline_runs_touch_updated_at_trg
  ON demo_pipeline_runs;
CREATE TRIGGER demo_pipeline_runs_touch_updated_at_trg
  BEFORE UPDATE ON demo_pipeline_runs
  FOR EACH ROW EXECUTE FUNCTION demo_pipeline_runs_touch_updated_at();

-- RLS: only the tenant that owns the run can read it. The service
-- role (used by the API) bypasses RLS as usual. We don't grant
-- INSERT/UPDATE to anon/authenticated — only the API writes.
ALTER TABLE demo_pipeline_runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY demo_pipeline_runs_tenant_select
  ON demo_pipeline_runs FOR SELECT
  USING (
    tenant_id IN (
      SELECT tenant_id FROM tenant_members WHERE user_id = auth.uid()
    )
  );
