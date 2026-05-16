-- 0131_total_trade_legal_data.sql
--
-- Dati legali reali di Total Trade S.r.l. (da visura camerale),
-- sostituiscono i placeholder demo ereditati dal seed base
-- ("SolarLead Demo S.r.l.", P.IVA IT12345678901, Via dei Pannelli 12).
-- Compaiono nel footer del portale lead.

UPDATE tenants
SET legal_name = 'Total Trade S.r.l.',
    vat_number = 'IT07874501211',
    legal_address = 'Via Alessandro Scarlatti 105, 80127 Napoli (NA)',
    updated_at = now()
WHERE id = 'df08df04-4c90-4613-b21e-80879fc958d1';
