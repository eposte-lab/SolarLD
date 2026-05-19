-- 0140 — Un soggetto per AZIENDA, non per tetto.
--
-- `subjects` aveva UNIQUE (tenant_id, roof_id): un solo soggetto/lead
-- per tetto. Sbagliato per gli immobili commerciali — un centro
-- commerciale ospita molte aziende sullo stesso tetto fisico, e Google
-- Solar le riporta correttamente tutte allo stesso `roof`. Il vincolo
-- collassava tutte le aziende co-localizzate in un unico lead.
--
-- La nuova chiave di unicità è l'azienda: `pii_hash`
-- (= sha256(business_name|google_place_id)), che L6 valorizza già su
-- ogni soggetto del funnel. Più soggetti possono ora condividere un
-- roof_id; ognuno è un lead distinto.

BEGIN;

ALTER TABLE subjects DROP CONSTRAINT IF EXISTS subjects_tenant_id_roof_id_key;

ALTER TABLE subjects
  ADD CONSTRAINT subjects_tenant_id_pii_hash_key UNIQUE (tenant_id, pii_hash);

COMMIT;
