-- 0124_total_trade_logo.sql
--
-- Valorizza brand_logo_url per il tenant Total Trade
-- (df08df04-4c90-4613-b21e-80879fc958d1, ex "SolarLead Demo").
--
-- Il logo è committato come asset statico del lead-portal
-- (apps/lead-portal/public/total-trade-logo.png) ed è quindi servito
-- all'URL pubblico stabile sotto. Header del portale + footer della
-- sezione EPC leggono già brand_logo_url: una volta valorizzato il
-- logo appare senza ulteriori modifiche di codice.

UPDATE tenants
SET brand_logo_url = 'https://solar-ld-lead-portal.vercel.app/total-trade-logo.png',
    updated_at = now()
WHERE id = 'df08df04-4c90-4613-b21e-80879fc958d1';
