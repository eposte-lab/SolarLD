-- ============================================================
-- Seed Total Trade go-live (primo test reale sul mercato)
-- ============================================================
-- Documentazione operativa della configurazione tenant Total Trade,
-- applicata via Supabase MCP il 2026-05-15. NON è una migration —
-- è un riferimento per: (a) replicare il setup su staging, (b) audit
-- in caso di rollback, (c) onboarding di un secondo cliente con
-- modello commerciale simile (EPC industriale).
--
-- Tenant ID: df08df04-4c90-4613-b21e-80879fc958d1 (ex "SolarLead Demo",
-- ora "Total Trade"). Decisione utente: trasformare il demo invece
-- di creare un tenant nuovo, per riusare i lead di test già scrappati.

-- ── 1. Tenant config ────────────────────────────────────────────────
UPDATE tenants SET
  business_name = 'Total Trade',
  website_url = 'https://www.totaltrade.it',
  email_from_name = 'Total Trade',
  brand_primary_color = '#183054',                -- navy Total Trade (estratto dal logo) — vedi migration 0125
  brand_logo_url = 'https://portale.solarlead.it/total-trade-logo.png',  -- sottodominio portale — vedi migration 0124, 0129
  legal_name = 'Total Trade S.r.l.',              -- visura camerale — vedi migration 0131
  vat_number = 'IT07874501211',                    -- P.IVA Total Trade — vedi migration 0131
  legal_address = 'Via Alessandro Scarlatti 105, 80127 Napoli (NA)',  -- sede legale — vedi migration 0131
  contact_email = 'info@totaltrade.it',           -- email contatto — vedi migration 0132
  email_signature = 'Il team Total Trade',         -- firma follow-up — vedi migration 0133
  privacy_policy_url = NULL,                       -- fallback /privacy SolarLead
  appointment_webhook_url = NULL,                  -- da definire (HubSpot/Pipedrive/email)
  daily_target_send_cap = 50,                      -- primo test conservativo
  epc_enabled = true,
  status = 'active',
  updated_at = now()
WHERE id = 'df08df04-4c90-4613-b21e-80879fc958d1';

-- ── 2. Sorgente module: settori EPC industriale + NA ─────────────────
UPDATE tenant_modules SET config = jsonb_set(
  jsonb_set(config,
    '{target_wizard_groups}',
    '["industry_heavy","industry_light","logistics","retail_gdo","hospitality_large","automotive"]'::jsonb
  ),
  '{province}',
  '["NA"]'::jsonb
), updated_at = now()
WHERE tenant_id = 'df08df04-4c90-4613-b21e-80879fc958d1'
  AND module_key = 'sorgente';

-- ── 3. Email templates riscritti con tono Total Trade ────────────────
-- Aggiornati via UPDATE diretto su 3 righe (4ef31c9f, 351860db, 126359d6).
-- I template originali "B2B Aziende — *" sono stati rinominati e
-- riscritti come "Total Trade — *" con copy specifico EPC:
--   - "Total Trade — Impianto gratuito EPC"          (cold opening)
--   - "Total Trade — Risparmio 20% bolletta"         (ROI angle)
--   - "Total Trade — Sostenibilità + EPC"            (ESG angle)
-- I 6 template restanti (Base professionale, A/B Variante, Condomini)
-- restano disponibili come template alternativi.

-- ── 4. Cose ancora da fare PRIMA del primo invio reale ─────────────
-- 4.1 Asset da cliente:
--     - Logo: estratto dal brochure PDF, committato come asset statico
--       apps/lead-portal/public/total-trade-logo.png — brand_logo_url
--       valorizzato dalla migration 0124. DONE.
--     - Colore primario: navy #183054 estratto dal logo, agganciato a
--       portale/dashboard/email via migration 0125. DONE (eventuale
--       hex ufficiale da brand book lo sostituisce in /settings/branding).
--     - Visura camerale → legal_name, vat_number, codice_fiscale, legal_address, numero_cciaa
--     - URL privacy policy Total Trade → privacy_policy_url (se ne hanno una)
--     - URL CRM webhook → appointment_webhook_url
-- 4.2 Dominio email outreach (es. mail.totaltrade.it):
--     - DNS records SPF/DKIM/DMARC (richiede accesso DNS lato cliente)
--     - INSERT in tenant_email_domains + verify via API
--     - Smartlead enroll: warmup 14gg a 40 email/giorno
-- 4.3 Prima scansione:
--     - Creare scan_schedule via UI /territorio: name="NA — EPC industriale primo test",
--       daily_cap=50, frequency_days=1, sector_filters=[6 settori], territory_ids=[]
-- 4.4 Verifica end-to-end:
--     - Compilo sopralluogo su un lead di test → webhook firing → CRM riceve payload
--     - Mail-tester score ≥ 8/10
--     - Render video produttivo: pannelli dentro il tetto, no overflow
