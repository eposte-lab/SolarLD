-- 0129_total_trade_logo_subdomain.sql
--
-- Il portale lead ora vive sul sottodominio custom portale.solarlead.it.
-- Aggiorna brand_logo_url del tenant Total Trade dal vecchio dominio
-- .vercel.app al dominio definitivo. Il vecchio URL resta valido (Vercel
-- mantiene l'alias .vercel.app), ma usiamo il dominio custom per coerenza.

UPDATE tenants
SET brand_logo_url = 'https://portale.solarlead.it/total-trade-logo.png',
    updated_at = now()
WHERE id = 'df08df04-4c90-4613-b21e-80879fc958d1';
