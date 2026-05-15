-- 0128_tenant_test_recipient_override.sql
--
-- Aggiunge tenants.test_recipient_override: quando valorizzato, OGNI
-- email outreach del tenant (step 1 manuale + follow-up automatici da
-- cron) viene rediretta a questo indirizzo invece che al lead reale.
-- Usato per lo scenario di test "demo della demo" prima del go-live.
-- A regime resta NULL.

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS test_recipient_override TEXT;
