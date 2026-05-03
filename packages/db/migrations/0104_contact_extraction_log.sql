-- ============================================================
-- 0104 — contact_extraction_log (GDPR audit trail, FLUSSO 1 v3)
-- ============================================================
-- Per ogni contatto pubblico estratto da L2 scraping, registra:
--   * il valore (email/telefono/PEC),
--   * la fonte (URL, tipo),
--   * il metodo (regex_html / json_ld / pagine_bianche / opencorporates / linkedin),
--   * timestamp.
--
-- Serve l'endpoint GET /api/gdpr/export per rispondere alla domanda
-- "dove avete preso questa email?" in caso di richiesta dell'interessato
-- (art. 15 GDPR — diritto di accesso).
--
-- L2 scrive qui best-effort: se questa migration non è ancora salita
-- l'agent non crasha (catch InsertException nel codice di L2).

CREATE TABLE IF NOT EXISTS contact_extraction_log (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  candidate_id       UUID NOT NULL REFERENCES scan_candidates(id) ON DELETE CASCADE,
  contact_value      TEXT NOT NULL,
  contact_type       TEXT NOT NULL CHECK (contact_type IN ('email', 'phone', 'pec', 'whatsapp')),
  source_url         TEXT,
  source_type        TEXT NOT NULL,  -- 'website' | 'pagine_bianche' | 'opencorporates' | 'linkedin' | 'manual'
  extraction_method  TEXT NOT NULL,  -- 'regex_html' | 'json_ld' | 'mailto_link' | 'api_lookup' | 'manual'
  confidence         TEXT,           -- 'alta' | 'media' | NULL
  extracted_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cel_tenant_candidate
  ON contact_extraction_log(tenant_id, candidate_id);

CREATE INDEX IF NOT EXISTS idx_cel_contact_value
  ON contact_extraction_log(contact_value);

CREATE INDEX IF NOT EXISTS idx_cel_extracted_at
  ON contact_extraction_log(extracted_at DESC);

ALTER TABLE contact_extraction_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS cel_tenant_select ON contact_extraction_log;
CREATE POLICY cel_tenant_select ON contact_extraction_log
  FOR SELECT
  USING (tenant_id = auth_tenant_id());

-- Service role bypasses RLS for inserts from the worker.

COMMENT ON TABLE contact_extraction_log IS
  'GDPR audit trail: una riga per ogni contatto pubblico estratto da L2 scraping. Risponde a "dove avete preso questa email?" in caso di richiesta dell''interessato (art. 15 GDPR).';

COMMENT ON COLUMN contact_extraction_log.source_url IS
  'URL della pagina da cui è stato estratto il contatto. Per Pagine Bianche / OpenCorporates / LinkedIn è la pagina di ricerca o il profilo aziendale.';

COMMENT ON COLUMN contact_extraction_log.confidence IS
  'Da extract_best_email: "alta" per ruoli nominati (direzione@, amministrazione@), "media" per generici (info@), NULL per phone/pec.';
