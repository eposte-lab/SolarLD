-- ============================================================
-- 0121 — tenants.website_url
-- ============================================================
-- Sprint client-feedback: il logo header del portale lead deve
-- essere CLICCABILE e portare al sito del tenant (es. clicco su
-- logo Total Trade → vado a total-trade.it).
--
-- Aggiunge anche un canale per l'extract-branding endpoint per
-- memorizzare l'URL già usato in onboarding senza dover
-- re-digitarlo ogni volta.

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS website_url TEXT;

COMMENT ON COLUMN tenants.website_url IS
  'URL pubblico del sito aziendale del tenant. Usato per: (a) target del logo cliccabile sul portale lead, (b) input default per extract-branding endpoint, (c) eventuale futura riprova auto-refresh logo.';
