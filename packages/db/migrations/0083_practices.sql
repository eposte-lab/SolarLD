-- GSE Practices Module — Livello 1 schema (Sprint 1).
--
-- Models the post-firma-contratto burocrazia for an installer:
--   1. `practices` — one row per (lead, signed contract). Captures the
--      installation-level data needed by every document (impianto power,
--      POD, distributore, catastali, componenti). One per lead in
--      Sprint 1; UNIQUE(lead_id) makes the bottone "Crea pratica"
--      idempotent (second click → conflict, redirect to existing).
--   2. `practice_documents` — one row per (practice, template_code).
--      Stores the rendered PDF URL, the snapshot of the data used at
--      render time, and the document lifecycle status (draft → reviewed
--      → sent → accepted/rejected). UNIQUE(practice_id, template_code)
--      makes "Rigenera" idempotent (UPSERT semantics).
--   3. `tenant_practice_counters` + `next_practice_seq()` RPC — the same
--      atomic-counter pattern as 0081_lead_quotes (preventivo). Avoids
--      the SELECT-MAX+1 race when two installers click "Crea pratica"
--      on the same tenant within the same transaction window.
--
-- Document codes (Sprint 1):
--   'dm_37_08'             — Dichiarazione di conformità DM 37/08
--   'comunicazione_comune' — Comunicazione fine lavori al Comune
-- Sprint 2 will add: 'tica_edist', 'tica_areti', 'tica_unareti',
--   'modello_unico', 'schema_unifilare', 'transizione_50'.

CREATE TABLE IF NOT EXISTS practices (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id uuid NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  -- Source preventivo. Optional because in theory a practice could be
  -- opened on a lead without a formal quote (rare), but in practice
  -- the bottone "Crea pratica" only appears post-`feedback=contract_signed`
  -- which today implies a quote was issued.
  quote_id uuid REFERENCES lead_quotes(id) ON DELETE SET NULL,
  -- Human-friendly identifier, e.g. "SOLE/2026/0042". The tenant
  -- abbreviation is computed from tenants.business_name at issue time
  -- and frozen here — even if the tenant later renames, prior practice
  -- numbers stay legible on archived PDFs.
  practice_number text NOT NULL,
  practice_seq int NOT NULL,
  status text NOT NULL DEFAULT 'in_preparation' CHECK (
    status IN (
      'in_preparation',   -- documents being generated
      'documents_ready',  -- all docs rendered, awaiting installer review
      'documents_sent',   -- installer has marked docs as sent to authorities
      'in_progress',      -- waiting on authority response
      'completed',        -- all authorities responded positively
      'blocked',          -- at least one rejection requires re-work
      'cancelled'         -- contract rescinded
    )
  ),
  -- Impianto snapshot (denormalized from lead_quote.manual_fields +
  -- the form fields the installer fills in the "Crea pratica" modal).
  impianto_potenza_kw numeric(10,2) NOT NULL,
  impianto_pannelli_count int,
  impianto_pod text,
  impianto_distributore text NOT NULL CHECK (
    impianto_distributore IN ('e_distribuzione', 'areti', 'unareti', 'altro')
  ),
  impianto_data_inizio_lavori date,
  impianto_data_fine_lavori date,
  -- Dati catastali — manual entry in Sprint 1; in a future sprint we
  -- can pull these from Google Solar API or a cadastre lookup.
  catastale_foglio text,
  catastale_particella text,
  catastale_subalterno text,
  -- Components (panels/inverter/accumulo). JSONB because the schema
  -- evolves frequently as new product families ship; we don't want
  -- to ALTER TABLE every time a new field is added. Mirror of the
  -- shape stored in lead_quotes.manual_fields.tech_*.
  componenti_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- Snapshot of the full mapper context at creation time. PDFs can
  -- be re-rendered from this without re-loading lead/subject/roof
  -- (which may have mutated since).
  data_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  -- Per-tenant unique practice number.
  UNIQUE (tenant_id, practice_number),
  -- One practice per lead (Sprint 1 simplification — relax later if
  -- multi-impianto-per-lead becomes a thing).
  UNIQUE (lead_id)
);

CREATE INDEX IF NOT EXISTS idx_practices_tenant_status
  ON practices (tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_practices_tenant_created
  ON practices (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_practices_lead
  ON practices (lead_id);

-- Auto-bump updated_at (mirrors 0081 pattern).
CREATE OR REPLACE FUNCTION practices_touch_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS practices_touch_updated_at_trg ON practices;
CREATE TRIGGER practices_touch_updated_at_trg
  BEFORE UPDATE ON practices
  FOR EACH ROW EXECUTE FUNCTION practices_touch_updated_at();


-- ---------------------------------------------------------------------
-- practice_documents — one row per (practice, template_code).
-- ---------------------------------------------------------------------
-- tenant_id is denormalized from practices for RLS efficiency (avoids
-- a JOIN in the policy).

CREATE TABLE IF NOT EXISTS practice_documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  practice_id uuid NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  template_code text NOT NULL,
  template_version text NOT NULL DEFAULT 'v1',
  status text NOT NULL DEFAULT 'draft' CHECK (
    status IN (
      'draft',     -- generated by the system, awaiting installer review
      'reviewed',  -- installer has reviewed and approved
      'sent',      -- installer has sent to the destination authority
      'accepted',  -- authority responded OK
      'rejected',  -- authority requested changes
      'amended',   -- modified after rejection, ready to re-send
      'completed'  -- final positive outcome
    )
  ),
  -- Public/signed URL of the rendered PDF. Null until the worker has
  -- finished rendering and uploading.
  pdf_url text,
  -- Bucket-relative path for re-signing without recomputing the path.
  pdf_storage_path text,
  -- The mapper context at render time — reproducible re-render.
  auto_data_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- Free-form fields the installer typed/edited per document.
  manual_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- Populated when the worker fails (network, weasyprint OOM, etc.) so
  -- the dashboard can surface the error and offer "Rigenera".
  generation_error text,
  generated_at timestamptz,
  sent_at timestamptz,
  accepted_at timestamptz,
  rejected_at timestamptz,
  rejection_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (practice_id, template_code)
);

CREATE INDEX IF NOT EXISTS idx_practice_documents_practice_status
  ON practice_documents (practice_id, status);

CREATE INDEX IF NOT EXISTS idx_practice_documents_tenant
  ON practice_documents (tenant_id, created_at DESC);

CREATE OR REPLACE FUNCTION practice_documents_touch_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS practice_documents_touch_updated_at_trg
  ON practice_documents;
CREATE TRIGGER practice_documents_touch_updated_at_trg
  BEFORE UPDATE ON practice_documents
  FOR EACH ROW EXECUTE FUNCTION practice_documents_touch_updated_at();


-- ---------------------------------------------------------------------
-- Per-tenant atomic counter for practice_seq (mirrors 0081's pattern).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tenant_practice_counters (
  tenant_id uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  last_seq int NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION next_practice_seq(p_tenant_id uuid)
RETURNS int LANGUAGE plpgsql AS $$
DECLARE v_seq int;
BEGIN
  INSERT INTO tenant_practice_counters (tenant_id, last_seq)
    VALUES (p_tenant_id, 1)
  ON CONFLICT (tenant_id) DO UPDATE
    SET last_seq = tenant_practice_counters.last_seq + 1,
        updated_at = now()
  RETURNING last_seq INTO v_seq;
  RETURN v_seq;
END;
$$;


-- ---------------------------------------------------------------------
-- RLS — same pattern as 0081_lead_quotes: members of the tenant can
-- SELECT; the service role (API) bypasses RLS for INSERT/UPDATE.
-- ---------------------------------------------------------------------

ALTER TABLE practices ENABLE ROW LEVEL SECURITY;

CREATE POLICY practices_tenant_select
  ON practices FOR SELECT
  USING (
    tenant_id IN (
      SELECT tenant_id FROM tenant_members WHERE user_id = auth.uid()
    )
  );

ALTER TABLE practice_documents ENABLE ROW LEVEL SECURITY;

CREATE POLICY practice_documents_tenant_select
  ON practice_documents FOR SELECT
  USING (
    tenant_id IN (
      SELECT tenant_id FROM tenant_members WHERE user_id = auth.uid()
    )
  );

ALTER TABLE tenant_practice_counters ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_practice_counters_tenant_select
  ON tenant_practice_counters FOR SELECT
  USING (
    tenant_id IN (
      SELECT tenant_id FROM tenant_members WHERE user_id = auth.uid()
    )
  );

-- ---------------------------------------------------------------------
-- Comments — discoverable in Supabase studio table list.
-- ---------------------------------------------------------------------

COMMENT ON TABLE practices IS
  'GSE practice (post-firma-contratto). One per lead. Aggregates impianto/cliente/installatore data needed by all generated documents.';

COMMENT ON TABLE practice_documents IS
  'Generated documents for a practice (DM 37/08, Comunicazione Comune in Sprint 1; TICA/Modello Unico/Schema unifilare/Transizione 5.0 in Sprint 2).';

COMMENT ON COLUMN practices.componenti_data IS
  'JSONB snapshot of components (panels/inverter/accumulo). Mirrors lead_quotes.manual_fields.tech_* shape.';

COMMENT ON COLUMN practices.data_snapshot IS
  'Full PracticeDataMapper context at creation time. Documents can be re-rendered from this without re-querying lead/subject/roof.';

COMMENT ON COLUMN practice_documents.pdf_url IS
  'Public/signed URL of the rendered PDF. Null until the arq worker finishes rendering.';

COMMENT ON COLUMN practice_documents.generation_error IS
  'Populated when the render task fails. Dashboard surfaces this + offers "Rigenera".';

COMMENT ON FUNCTION next_practice_seq(uuid) IS
  'Atomically increment and return the per-tenant practice sequence. Race-safe under concurrent saves (mirrors next_quote_seq pattern).';
