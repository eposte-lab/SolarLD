-- 0133_tenant_email_signature.sql
--
-- Aggiunge tenants.email_signature: la firma usata nei follow-up al
-- posto del segnaposto {{firma}}. È una firma unica per tenant,
-- configurabile dal dashboard. Total Trade riceve un default sensato.

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS email_signature TEXT;

UPDATE tenants
SET email_signature = 'Il team Total Trade',
    updated_at = now()
WHERE id = 'df08df04-4c90-4613-b21e-80879fc958d1';
