-- Sprint 2: extra per-practice fields needed by Modello Unico, TICA, and
-- Transizione 5.0 templates.
--
-- Why JSONB instead of typed columns: these are template-specific,
-- evolve frequently as we add document families, and many are
-- conditional (IBAN only matters for ritiro dedicato; codice
-- identificativo connessione only matters for Modello Unico Parte II;
-- regime ritiro is mutually exclusive with utente_dispacciamento_*).
-- A typed schema would balloon to 25+ nullable columns the moment we
-- add Transizione 5.0 — JSONB keeps the column count flat.
--
-- Schema documented in apps/api/src/services/practice_data_mapper.py
-- under the EXTRAS_SHAPE comment block. Keys are stable; new docs add
-- new sub-keys but never rename existing ones.
--
-- Examples of what lives here (Sprint 2):
--   "iban":                              "IT60X0542811101000000123456"
--   "regime_ritiro":                     "gse_po" | "gse_pmg" | "mercato"
--   "qualita_richiedente":               "proprietario" | "amministratore" | ...
--   "denominazione_impianto":            "FV Acme HQ"
--   "tipologia_struttura":               "edificio" | "fuori_terra"
--   "codice_identificativo_connessione": "12345678" (Modello Unico Pt. II)
--   "codice_rintracciabilita":           "ABCD1234"
--   "potenza_immissione_kw":             50.0
--   "configurazione_accumulo":           "lato_produzione_mono" | ...
--   "utente_dispacciamento":             { "ragione_sociale", "cf", "piva", "pec", "email", "codice_contratto" }
--   "transizione50":                     { "ateco", "tep_anno", "perc_riduzione", ... }

ALTER TABLE practices
  ADD COLUMN IF NOT EXISTS extras JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN practices.extras IS
  'Template-specific fields (IBAN, regime ritiro, codice identificativo connessione, qualita richiedente, ecc.). Schema in practice_data_mapper.py EXTRAS_SHAPE.';
