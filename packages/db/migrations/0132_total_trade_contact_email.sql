-- 0132_total_trade_contact_email.sql
--
-- Email di contatto reale di Total Trade, sostituisce il placeholder
-- demo (demo@solarlead.it). Usata nel footer del portale ("Contatta
-- Total Trade") e come reply-to di fallback per l'outreach.

UPDATE tenants
SET contact_email = 'info@totaltrade.it',
    updated_at = now()
WHERE id = 'df08df04-4c90-4613-b21e-80879fc958d1';
