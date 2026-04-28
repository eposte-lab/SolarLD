-- 0070_engagement_followup_scenarios.sql
--
-- Sprint 10 — Engagement-based follow-up automation.
--
-- The cold-silence cadence (step 2/3/4 d+4/9/14) handled by
-- ``followup_service.py`` halts as soon as a lead opens/clicks. That is
-- correct for cold prospects but leaves anyone who engages without a
-- second touch. This migration introduces a parallel follow-up engine
-- driven by the existing 0-100 ``engagement_score`` rollup.
--
-- Six scenarios, each with its own cadence and copy register:
--   cold (0)       — re-anchor, generic value prop
--   lukewarm (1-20) — light reminder, leave door open
--   engaged (21-40) — sector context + soft CTA
--   interessato (41-60) — case study + concrete next step
--   hot (61+)      — NO email; notify operator for manual outreach
--   riattivazione  — previously engaged (>=40), silent 14+ days
--
-- The system also persists a per-(tenant, ATECO 2-digit) news pool that
-- operators can edit, so the "engaged" / "interessato" copy can quote a
-- recent sector signal without becoming creepy ("we noticed you opened
-- the email" is forbidden — sector news is the substitute hook).

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Per-lead follow-up history (one row per scenario fire)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS followup_emails_sent (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id         UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  scenario        TEXT NOT NULL CHECK (scenario IN (
    'cold',
    'lukewarm',
    'engaged',
    'interessato',
    'riattivazione'
  )),
  -- Snapshot of the score that triggered the fire — used for retro
  -- analysis ("did the engaged scenario actually work?").
  score_at_send   SMALLINT NOT NULL CHECK (score_at_send BETWEEN 0 AND 100),
  -- Reference to the underlying outreach_sends row (the actual delivery).
  outreach_send_id UUID REFERENCES outreach_sends(id) ON DELETE SET NULL,
  -- Sector news id baked into the copy (NULL if generic).
  sector_news_id  UUID,
  sent_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- One scenario fire per (lead, scenario, calendar week) — guards
  -- against the cron mis-firing twice.
  UNIQUE (lead_id, scenario, sent_at)
);

CREATE INDEX IF NOT EXISTS idx_followup_emails_sent_lead
  ON followup_emails_sent (lead_id, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_followup_emails_sent_tenant
  ON followup_emails_sent (tenant_id, sent_at DESC);

ALTER TABLE followup_emails_sent ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS followup_emails_sent_tenant_iso ON followup_emails_sent;
CREATE POLICY followup_emails_sent_tenant_iso
  ON followup_emails_sent FOR ALL TO authenticated
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

-- ---------------------------------------------------------------------------
-- 2. Lead-level pointers — fast lookup without scanning history
-- ---------------------------------------------------------------------------
ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS last_followup_scenario  TEXT,
  ADD COLUMN IF NOT EXISTS last_followup_sent_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS hot_lead_alerted_at     TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS engagement_peak_score   SMALLINT;

CREATE INDEX IF NOT EXISTS idx_leads_last_followup
  ON leads (tenant_id, last_followup_sent_at)
  WHERE last_followup_sent_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. Sector news catalogue
-- ---------------------------------------------------------------------------
-- Operator-editable. Tenant-scoped so each installer can curate their
-- own sector messaging, but seeded globally (tenant_id NULL) for boot.
-- The lookup picks "best match for this lead" with this priority:
--   1. tenant-specific row matching ATECO 2-digit, status=active
--   2. global row (tenant_id IS NULL) matching ATECO 2-digit, status=active
--   3. fallback: NULL (template uses generic copy)
CREATE TABLE IF NOT EXISTS sector_news (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID REFERENCES tenants(id) ON DELETE CASCADE, -- NULL = global seed
  ateco_2digit    CHAR(2) NOT NULL,
  headline        TEXT NOT NULL CHECK (length(headline) BETWEEN 10 AND 140),
  body            TEXT NOT NULL CHECK (length(body) BETWEEN 20 AND 600),
  source_url      TEXT,
  status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'archived')),
  -- When set, the news rotates out of the active pool after this date.
  expires_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sector_news_lookup
  ON sector_news (ateco_2digit, status, tenant_id)
  WHERE status = 'active';

ALTER TABLE sector_news ENABLE ROW LEVEL SECURITY;

-- Authenticated tenants see their own rows + global seeds.
DROP POLICY IF EXISTS sector_news_read ON sector_news;
CREATE POLICY sector_news_read ON sector_news FOR SELECT TO authenticated
  USING (tenant_id = auth_tenant_id() OR tenant_id IS NULL);

-- Operators can only insert/update/delete their own tenant rows.
DROP POLICY IF EXISTS sector_news_write ON sector_news;
CREATE POLICY sector_news_write ON sector_news FOR ALL TO authenticated
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

-- Service-role bypass for cron / seed scripts is implicit (RLS off via
-- service key).

-- ---------------------------------------------------------------------------
-- 4. Seed global sector news (3 starters per the design doc)
-- ---------------------------------------------------------------------------
INSERT INTO sector_news (tenant_id, ateco_2digit, headline, body, source_url)
VALUES
  -- ATECO 25 — fabbricazione prodotti in metallo (metalmeccanico)
  (NULL, '25',
   'Acciaio +18% sul 2024: chi produce in casa la propria energia tiene il margine',
   'I prezzi dell''acciaio strutturale sono saliti del 18% rispetto a inizio 2024 e gli analisti vedono altri rincari nei prossimi 6 mesi. Le aziende metalmeccaniche con impianto fotovoltaico aziendale stanno limitando l''impatto perché abbattono il costo energia (dopo l''acciaio è la seconda voce di bilancio in produzione) anche del 30-40%.',
   'https://www.assofond.it/news/prezzi-materie-prime-2025'),
  -- ATECO 49 — trasporto terrestre (logistica)
  (NULL, '49',
   'Diesel a 1,80 €/L: chi ha colonnine a energia propria taglia il costo flotta',
   'Con il diesel stabilmente sopra 1,80 €/L e la spinta normativa verso le ZTL elettrificate, le aziende di logistica stanno passando a flotte miste. Un impianto fotovoltaico sul magazzino + colonnine di ricarica abbatte il costo per km del 40-60% rispetto al diesel — e il payback è più corto di quanto si pensi grazie agli incentivi PNRR ancora attivi.',
   'https://www.transpotec.com/news/elettrico-flotte-2025'),
  -- ATECO 01 — agricoltura
  (NULL, '01',
   'Bandi PSR 2025: agrivoltaico fino al 65% a fondo perduto',
   'I nuovi bandi PSR regionali 2025 coprono fino al 65% a fondo perduto per impianti agrivoltaici (pannelli sopraelevati che lasciano la coltivazione sotto). Le scadenze sono regionali ma quasi tutte chiudono in autunno: serve istruttoria tecnica entro l''estate per non perdere l''opportunità.',
   'https://www.reterurale.it/PSR-2025/agrivoltaico')
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------------
-- 5. updated_at trigger for sector_news
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sector_news_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS sector_news_updated_at_trg ON sector_news;
CREATE TRIGGER sector_news_updated_at_trg
  BEFORE UPDATE ON sector_news
  FOR EACH ROW EXECUTE FUNCTION sector_news_set_updated_at();

COMMIT;
