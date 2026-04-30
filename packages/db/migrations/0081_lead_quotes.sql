-- Formal quotes (preventivo) generated from a hot lead.
--
-- Closes the funnel for the installer-tenant: from "lead caldo nella
-- dashboard" → "PDF firmabile da inviare al cliente" without leaving
-- SolarLead. Until now the only artefacts on a lead were the email
-- + the public landing page; everything else (Word/Excel preventivo,
-- prezzi, modalità di pagamento) lived in the installer's head or in
-- a separate template they edited by hand. This table captures the
-- preventivo as a first-class object.
--
-- Schema rationale (hybrid typed + JSONB):
--   * `preventivo_number`, `preventivo_seq`, `version`, `status` are
--     typed columns — we sort, filter, and unique-index on them.
--   * `auto_fields` (snapshot of tenant/azienda/solar/econ/render at
--     issue time) and `manual_fields` (what the installer typed:
--     commerciale, brand, prezzo, pagamento, tempi, note) are JSONB.
--     These have ~30+ keys and the template evolves; we don't want
--     to ALTER every time a new field is added.
--
-- Versioning is immutable. Each "Salva e genera PDF" creates a NEW
-- row with version = max+1; previous versions stay around with
-- status='superseded'. Audit trail + the dashboard can show
-- "v3 of 5" history.
--
-- preventivo_number race-safety: we don't compute MAX(seq)+1 at the
-- application layer (would race under concurrent saves). Instead a
-- per-tenant counter table + RPC `next_quote_seq` does an atomic
-- INSERT … ON CONFLICT … RETURNING that's guaranteed monotonic
-- regardless of transaction interleaving.

CREATE TABLE IF NOT EXISTS lead_quotes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id uuid NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  -- Human-friendly identifier, e.g. "2026/PV/0042". Stable for the
  -- lifetime of the row — even if formatting changes in the future,
  -- this stays the way it was printed on the original PDF.
  preventivo_number text NOT NULL,
  -- Raw monotonic integer used to compute the next number. Decoupled
  -- from the formatted string so reformatting (e.g. switching to
  -- "PV-2026-0042") doesn't break sequencing.
  preventivo_seq int NOT NULL,
  -- Per-lead version. v1 = first issued; v2 = re-edit & save; etc.
  version int NOT NULL DEFAULT 1,
  status text NOT NULL DEFAULT 'draft' CHECK (
    status IN ('draft', 'issued', 'superseded')
  ),
  -- Snapshot of every AUTO field at issue time: tenant_*, azienda_*
  -- (from subjects), solar_* (from solar_gate / roof analysis),
  -- econ_* (from roi_service.compute_roi), render_after_url, etc.
  -- Snapshotting (vs. re-reading at render time) means the PDF is
  -- reproducible even if the underlying lead row is later updated.
  auto_fields jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- The installer's input: commerciale_*, tech_* (panel/inverter
  -- brand+model), prezzo_*, incentivo_*, pagamento_*, tempi_*, note.
  -- Free-form by design — the template evolves, JSONB lets us add
  -- keys without migrating.
  manual_fields jsonb NOT NULL DEFAULT '{}'::jsonb,
  pdf_url text,
  -- Snapshot of leads.rendering_image_url at issue time. Cached here
  -- so re-rendering the PDF doesn't depend on the (possibly mutated)
  -- lead row.
  hero_url text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, preventivo_number)
);

-- Per-lead history lookup ("show me v3 of 5 with v1, v2 in dropdown").
CREATE INDEX IF NOT EXISTS idx_lead_quotes_lead
  ON lead_quotes (lead_id, version DESC);

-- Tenant-wide list ("all preventivi this installer issued, newest first").
CREATE INDEX IF NOT EXISTS idx_lead_quotes_tenant_created
  ON lead_quotes (tenant_id, created_at DESC);

-- Auto-bump updated_at on UPDATE.
CREATE OR REPLACE FUNCTION lead_quotes_touch_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS lead_quotes_touch_updated_at_trg
  ON lead_quotes;
CREATE TRIGGER lead_quotes_touch_updated_at_trg
  BEFORE UPDATE ON lead_quotes
  FOR EACH ROW EXECUTE FUNCTION lead_quotes_touch_updated_at();


-- ---------------------------------------------------------------------
-- Per-tenant monotonic counter for preventivo_seq.
--
-- Why a counter table + RPC instead of MAX(seq)+1: with two concurrent
-- saves on the same tenant, the SELECT MAX both see the same N, both
-- compute N+1, both INSERT — and one fails on the UNIQUE constraint
-- (worst case) or both succeed with duplicate user-visible numbers
-- (worst-worst case if the unique was relaxed). The INSERT … ON
-- CONFLICT DO UPDATE … RETURNING pattern is atomic at the row level
-- in Postgres, so it's monotonic regardless of how many concurrent
-- transactions are in flight.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tenant_quote_counters (
  tenant_id uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  last_seq int NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION next_quote_seq(p_tenant_id uuid)
RETURNS int LANGUAGE plpgsql AS $$
DECLARE v_seq int;
BEGIN
  INSERT INTO tenant_quote_counters (tenant_id, last_seq)
    VALUES (p_tenant_id, 1)
  ON CONFLICT (tenant_id) DO UPDATE
    SET last_seq = tenant_quote_counters.last_seq + 1,
        updated_at = now()
  RETURNING last_seq INTO v_seq;
  RETURN v_seq;
END;
$$;


-- ---------------------------------------------------------------------
-- RLS — same pattern as demo_pipeline_runs (0078): only the tenant's
-- members can SELECT; the service role (API server) bypasses RLS for
-- INSERT/UPDATE.
-- ---------------------------------------------------------------------

ALTER TABLE lead_quotes ENABLE ROW LEVEL SECURITY;

CREATE POLICY lead_quotes_tenant_select
  ON lead_quotes FOR SELECT
  USING (
    tenant_id IN (
      SELECT tenant_id FROM tenant_members WHERE user_id = auth.uid()
    )
  );

ALTER TABLE tenant_quote_counters ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_quote_counters_tenant_select
  ON tenant_quote_counters FOR SELECT
  USING (
    tenant_id IN (
      SELECT tenant_id FROM tenant_members WHERE user_id = auth.uid()
    )
  );

COMMENT ON TABLE lead_quotes IS
  'Formal preventivo (PDF) generated from a hot lead. Immutable versions; status=superseded marks prior revisions.';

COMMENT ON COLUMN lead_quotes.auto_fields IS
  'JSONB snapshot of tenant/azienda/solar/econ fields at issue time. PDF is reproducible from this + manual_fields.';

COMMENT ON COLUMN lead_quotes.manual_fields IS
  'JSONB of installer-typed values: commerciale_*, tech_* (panel/inverter brand+model), prezzo_*, pagamento_*, tempi_*, note.';

COMMENT ON FUNCTION next_quote_seq(uuid) IS
  'Atomically increment and return the per-tenant preventivo sequence. Race-safe under concurrent saves.';
