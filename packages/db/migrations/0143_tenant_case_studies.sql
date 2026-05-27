-- 0143_tenant_case_studies.sql
--
-- "Lavori realizzati" — portfolio/case-study per tenant, mostrato sia nel
-- dossier (portale) sia, in forma compatta (2 a rotazione), nelle email
-- outreach. Costruisce fiducia ("ecco cosa abbiamo già fatto") e spinge
-- al click verso il dossier.
--
-- `tenants.installations_count`: numero TOTALE di impianti realizzati,
-- mostrato come social proof ("X impianti realizzati") indipendentemente
-- da quanti case-study di dettaglio sono caricati.

CREATE TABLE IF NOT EXISTS tenant_case_studies (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  client_name   text NOT NULL,            -- es. "Hotel La Mela"
  story         text,                     -- breve testo / storia installazione
  image_url     text,                     -- foto impianto realizzato
  logo_url      text,                     -- logo cliente (opzionale)
  kwp           numeric,                  -- potenza impianto (opzionale)
  location      text,                     -- es. "Napoli" (opzionale)
  year          int,                      -- anno installazione (opzionale)
  display_order int NOT NULL DEFAULT 0,   -- ordinamento manuale
  active        boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tenant_case_studies_tenant_active_idx
  ON tenant_case_studies (tenant_id, active, display_order);

COMMENT ON TABLE tenant_case_studies IS
  'Lavori realizzati per tenant — case study mostrati su dossier + email.';

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS installations_count int
  CHECK (installations_count IS NULL OR installations_count >= 0);

COMMENT ON COLUMN tenants.installations_count IS
  'Numero totale impianti realizzati (social proof su dossier + email).';

-- RLS: i case study sono letti dal portale via service-role (public.py),
-- non direttamente dal client anon. Abilitiamo RLS e una policy
-- tenant-scoped per la dashboard (CRUD futuro), coerente con le altre
-- tabelle tenant.
ALTER TABLE tenant_case_studies ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_case_studies_tenant_rw ON tenant_case_studies;
CREATE POLICY tenant_case_studies_tenant_rw ON tenant_case_studies
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());
