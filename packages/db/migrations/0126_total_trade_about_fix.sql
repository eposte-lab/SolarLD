-- 0126_total_trade_about_fix.sql
--
-- Corregge la sezione "Chi siamo" del tenant Total Trade.
--
-- about_md / about_tagline / about_certifications erano copia demo
-- residua del vecchio tenant "SolarLead Demo": "Operiamo in Lombardia",
-- "amministratori di condominio", certificazione "MCS Certified" (ente
-- britannico). Niente di tutto ciò riguarda Total Trade — azienda di
-- Napoli, technical partner di ENI Plenitude, modello EPC.
--
-- Sostituiti con contenuto reale tratto dal brochure Total Trade.
-- L'operatore può rifinirli da /settings/branding/about.

UPDATE tenants
SET about_md = 'Total Trade è technical partner di ENI Plenitude. '
               'Realizziamo impianti fotovoltaici per le aziende con il '
               'modello EPC: l''investimento è interamente a nostro carico, '
               'l''azienda cliente non spende nulla e risparmia da subito '
               'sulla bolletta. A fine contratto l''impianto diventa di '
               'proprietà dell''azienda.',
    about_tagline = 'Facciamo la rivoluzione energetica, insieme.',
    about_certifications = ARRAY['Technical partner ENI Plenitude']::text[],
    updated_at = now()
WHERE id = 'df08df04-4c90-4613-b21e-80879fc958d1';
