-- 0095_demo_mock_enrichment_form_prefill.sql
--
-- Add form-prefill columns to demo_mock_enrichment so the test
-- pipeline dialog can rotate through seeded real Italian companies
-- on each open instead of always defaulting to MULTILOG. Backfill the
-- 10 existing rows with sector- and region-appropriate legal_names,
-- ATECO codes, addresses, and a plausible Italian decision-maker
-- name + role.
--
-- The new GET /v1/demo/random-seed endpoint reads from these columns
-- and returns ONE row per call. The dialog auto-fires on open
-- (different real company every time) and exposes a manual
-- "🎲 Cambia azienda" button for the operator to re-roll on demand.

ALTER TABLE demo_mock_enrichment
  ADD COLUMN IF NOT EXISTS legal_name TEXT,
  ADD COLUMN IF NOT EXISTS ateco_code TEXT,
  ADD COLUMN IF NOT EXISTS hq_address TEXT,
  ADD COLUMN IF NOT EXISTS decision_maker_name TEXT,
  ADD COLUMN IF NOT EXISTS decision_maker_role TEXT,
  ADD COLUMN IF NOT EXISTS decision_maker_email TEXT;

COMMENT ON COLUMN demo_mock_enrichment.legal_name IS
  'Plausible Italian legal name (S.r.l. / S.p.A.) used to prefill the test pipeline dialog. Sourced for the seeded demo VATs only; production hunter funnel reads the real legal name from Atoka.';

COMMENT ON COLUMN demo_mock_enrichment.decision_maker_email IS
  'Placeholder PEC-style address for the seeded demo VATs. The form recipient_email (where the test email is delivered) is always the operator-typed value — this column is purely for the personalisation of copy and the audit trail.';

-- Backfill the 10 seeded rows. Each gets:
--   * legal_name matching the sector + region
--   * ateco_code (real ATECO 2007 code derived from the description)
--   * hq_address copied from sede_operativa_address (same building)
--   * Italian decision-maker name (plausible, varies by region)
--   * decision_maker_role (Amministratore Delegato / Titolare / etc.)
--   * decision_maker_email (info@<slug>.it placeholder; MULTILOG keeps
--     its real PEC since it was already in the legacy DEFAULT_FORM)

UPDATE demo_mock_enrichment SET
  legal_name = 'Imballaggi Liguria S.r.l.', ateco_code = '16.24',
  hq_address = sede_operativa_address,
  decision_maker_name = 'Marco Rossi', decision_maker_role = 'Amministratore Delegato',
  decision_maker_email = 'info@imballaggiliguria.it'
WHERE vat_number = '01534568096';

UPDATE demo_mock_enrichment SET
  legal_name = 'Trasporti Toscana S.p.A.', ateco_code = '49.41',
  hq_address = sede_operativa_address,
  decision_maker_name = 'Giovanni Bianchi', decision_maker_role = 'Direttore Generale',
  decision_maker_email = 'amministrazione@trasportitoscana.it'
WHERE vat_number = '01845680974';

UPDATE demo_mock_enrichment SET
  legal_name = 'Ceramiche Emiliane S.r.l.', ateco_code = '23.31',
  hq_address = sede_operativa_address,
  decision_maker_name = 'Andrea Manfredini', decision_maker_role = 'Amministratore Delegato',
  decision_maker_email = 'info@ceramicheemiliane.it'
WHERE vat_number = '01956780360';

UPDATE demo_mock_enrichment SET
  legal_name = 'Carpenteria Metallica Bresciana S.r.l.', ateco_code = '25.11',
  hq_address = sede_operativa_address,
  decision_maker_name = 'Luca Galli', decision_maker_role = 'Titolare',
  decision_maker_email = 'info@carpenteriametallicabresciana.it'
WHERE vat_number = '02134560988';

UPDATE demo_mock_enrichment SET
  legal_name = 'Refrigerazione Veneta S.p.A.', ateco_code = '28.25',
  hq_address = sede_operativa_address,
  decision_maker_name = 'Roberto Marin', decision_maker_role = 'Amministratore Delegato',
  decision_maker_email = 'amministrazione@refrigerazioneveneta.it'
WHERE vat_number = '03245678908';

UPDATE demo_mock_enrichment SET
  legal_name = 'Cartotecnica Torinese S.r.l.', ateco_code = '17.21',
  hq_address = sede_operativa_address,
  decision_maker_name = 'Mario Ferraris', decision_maker_role = 'Amministratore Unico',
  decision_maker_email = 'info@cartotecnicatorinese.it'
WHERE vat_number = '04356780016';

UPDATE demo_mock_enrichment SET
  legal_name = 'Edilizia Mediterranea S.r.l.', ateco_code = '41.20',
  hq_address = sede_operativa_address,
  decision_maker_name = 'Salvatore Russo', decision_maker_role = 'Amministratore Delegato',
  decision_maker_email = 'direzione@ediliziamediterranea.it'
WHERE vat_number = '04834567891';

UPDATE demo_mock_enrichment SET
  legal_name = 'Tessuti Campani S.p.A.', ateco_code = '13.99',
  hq_address = sede_operativa_address,
  decision_maker_name = 'Antonio Esposito', decision_maker_role = 'Direttore Operativo',
  decision_maker_email = 'amministrazione@tessuticampani.it'
WHERE vat_number = '06578901218';

UPDATE demo_mock_enrichment SET
  legal_name = 'MULTILOG S.P.A.', ateco_code = '49.41',
  hq_address = COALESCE(sede_operativa_address, 'Zona Industriale ASI, 80023 Agglomerato Asi Pascarola NA'),
  decision_maker_name = 'Antonio De Luca', decision_maker_role = 'Amministratore Delegato',
  decision_maker_email = 'multilogspa@pec.it'
WHERE vat_number = '09881610019';

UPDATE demo_mock_enrichment SET
  legal_name = 'Pastificio Capitolino S.r.l.', ateco_code = '10.73',
  hq_address = sede_operativa_address,
  decision_maker_name = 'Lorenzo Conti', decision_maker_role = 'Amministratore Delegato',
  decision_maker_email = 'info@pastificiocapitolino.it'
WHERE vat_number = '11456781009';
