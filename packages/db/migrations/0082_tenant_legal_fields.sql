-- Tenant legal fields required by the GSE Practices module (Sprint 1).
--
-- Why these specific fields:
--   * `codice_fiscale` — distinct from `vat_number` (legacy field used in
--     0052 for GDPR footer); GSE/distributore portals require both.
--     Tenants whose CF == P.IVA simply duplicate the value.
--   * `numero_cciaa` — Camera di Commercio registration number, mandatory
--     in the DM 37/08 dichiarazione di conformità header.
--   * `responsabile_tecnico_*` — the iscritto-all'albo signatory whose
--     name + qualifica + albo number must appear on every dichiarazione
--     di conformità DM 37/08. Without these, the document cannot be
--     legally signed.
--
-- All nullable: existing tenants don't have these populated. The API
-- enforces them at document-generation time (422 with the missing field
-- list), not at write time — so onboarding flow stays untouched.

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS codice_fiscale TEXT,
  ADD COLUMN IF NOT EXISTS numero_cciaa TEXT,
  ADD COLUMN IF NOT EXISTS responsabile_tecnico_nome TEXT,
  ADD COLUMN IF NOT EXISTS responsabile_tecnico_cognome TEXT,
  ADD COLUMN IF NOT EXISTS responsabile_tecnico_codice_fiscale TEXT,
  ADD COLUMN IF NOT EXISTS responsabile_tecnico_qualifica TEXT,
  ADD COLUMN IF NOT EXISTS responsabile_tecnico_iscrizione_albo TEXT;

COMMENT ON COLUMN tenants.codice_fiscale IS
  'Codice fiscale of the legal entity. Required for GSE / distributore practices. May equal vat_number for societies.';

COMMENT ON COLUMN tenants.numero_cciaa IS
  'Camera di Commercio registration number (e.g. "MI-1234567"). Required in DM 37/08 header.';

COMMENT ON COLUMN tenants.responsabile_tecnico_nome IS
  'First name of the iscritto-all''albo technical lead who signs DM 37/08.';

COMMENT ON COLUMN tenants.responsabile_tecnico_cognome IS
  'Last name of the iscritto-all''albo technical lead.';

COMMENT ON COLUMN tenants.responsabile_tecnico_codice_fiscale IS
  'Codice fiscale of the technical lead (signatory).';

COMMENT ON COLUMN tenants.responsabile_tecnico_qualifica IS
  'Qualifica (e.g. "ingegnere elettrico", "perito industriale").';

COMMENT ON COLUMN tenants.responsabile_tecnico_iscrizione_albo IS
  'Albo membership reference (e.g. "Ordine Ingegneri Milano n. 1234"). Printed verbatim on the dichiarazione di conformità.';
