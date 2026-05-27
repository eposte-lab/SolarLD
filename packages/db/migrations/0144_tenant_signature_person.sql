-- 0144_tenant_signature_person.sql
--
-- Firma "persona reale" per le email outreach: nel B2B italiano una firma
-- con nome + ruolo + telefono diretto (+ foto) sposta il reply rate del
-- 25-40% rispetto a una firma azienda ("Il team X"). Aggiungiamo i campi
-- del referente sul tenant + l'area geografica per la social proof
-- ("X impianti realizzati in {area}").

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS signature_name      text,
  ADD COLUMN IF NOT EXISTS signature_role      text,
  ADD COLUMN IF NOT EXISTS signature_phone     text,
  ADD COLUMN IF NOT EXISTS signature_email     text,
  ADD COLUMN IF NOT EXISTS signature_photo_url text,
  ADD COLUMN IF NOT EXISTS installations_area  text;

COMMENT ON COLUMN tenants.signature_name IS 'Nome referente per la firma email (persona reale).';
COMMENT ON COLUMN tenants.signature_role IS 'Ruolo del referente (es. Responsabile commerciale).';
COMMENT ON COLUMN tenants.signature_phone IS 'Telefono diretto del referente (link tel:).';
COMMENT ON COLUMN tenants.signature_email IS 'Email del referente (link mailto:).';
COMMENT ON COLUMN tenants.signature_photo_url IS 'Foto piccola del referente (firma email).';
COMMENT ON COLUMN tenants.installations_area IS 'Area geografica per la social proof (es. Campania).';
