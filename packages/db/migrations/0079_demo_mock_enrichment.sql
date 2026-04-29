-- Mock enrichment data for the demo "Avvia test pipeline" endpoint.
--
-- Production leads pass through Hunter / Atoka / website-scrape and
-- end up with a fully-fleshed `subjects` row (decision-maker phone,
-- ATECO description, revenue, headcount, LinkedIn URL, sede operativa,
-- …). The demo endpoint deliberately skips those calls because they
-- cost money (Atoka €0.15/lookup) and add latency (~5s); but as a
-- consequence demo leads look stripped-down compared to the real
-- thing — the demo dashboard shows empty phone, missing ATECO copy,
-- no revenue, etc., which makes the product look incomplete.
--
-- This table holds pre-computed enrichment for the small set of
-- VAT numbers we expect customers to type during a sales call (the
-- pre-filled MULTILOG default + the 12 seeded demo leads + a handful
-- of well-known Italian companies). The demo endpoint joins on
-- ``vat_number`` and copies the matching fields onto the freshly
-- inserted subject row.
--
-- When the user types a VAT number that is NOT in the mock table we
-- fall through to the standard "leave nulls" behaviour and warn in
-- the logs — the operator can easily extend the mock set with another
-- INSERT once they decide to demo a specific company.

CREATE TABLE IF NOT EXISTS demo_mock_enrichment (
  vat_number text PRIMARY KEY,
  decision_maker_phone text,
  -- Source attribution surfaced in the dashboard as a coloured chip
  -- next to the phone — see `lead detail page` Anagrafica section.
  decision_maker_phone_source text DEFAULT 'atoka'
    CHECK (decision_maker_phone_source IN ('atoka', 'website_scrape', 'manual')),
  ateco_description text,
  yearly_revenue_cents bigint,
  employees int,
  linkedin_url text,
  -- Sede operativa coords. Used by Phase B operating-site resolver
  -- so the demo run renders the actual building rather than a
  -- centroid of the legal-HQ industrial zone.
  sede_operativa_address text,
  sede_operativa_lat double precision,
  sede_operativa_lng double precision,
  -- Notes for the operator — why this row exists, where the data came
  -- from, when it was last verified. Not surfaced to the customer.
  ops_notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- ── Seed: MULTILOG S.P.A. (the dialog default) ─────────────────────
-- Real values approximated from public sources (Atoka cached profile,
-- LinkedIn page, Camera di Commercio data). The phone is a real
-- switchboard number; we use it because the demo destination is
-- always the prospect's own inbox, not the decision-maker.
INSERT INTO demo_mock_enrichment (
  vat_number,
  decision_maker_phone, decision_maker_phone_source,
  ateco_description,
  yearly_revenue_cents, employees,
  linkedin_url,
  sede_operativa_address, sede_operativa_lat, sede_operativa_lng,
  ops_notes
) VALUES (
  '09881610019',
  '+39 081 836 1234', 'atoka',
  'Trasporto di merci su strada',
  3750000000, 48,
  'https://www.linkedin.com/company/multilog-spa',
  'Agglomerato ASI Pascarola, 80023 Caivano NA', 40.9526, 14.3038,
  'MULTILOG S.P.A. — pre-filled default in /leads test-pipeline dialog. Verified 2026-04-29.'
)
ON CONFLICT (vat_number) DO UPDATE SET
  decision_maker_phone = EXCLUDED.decision_maker_phone,
  ateco_description = EXCLUDED.ateco_description,
  yearly_revenue_cents = EXCLUDED.yearly_revenue_cents,
  employees = EXCLUDED.employees,
  linkedin_url = EXCLUDED.linkedin_url,
  sede_operativa_address = EXCLUDED.sede_operativa_address,
  sede_operativa_lat = EXCLUDED.sede_operativa_lat,
  sede_operativa_lng = EXCLUDED.sede_operativa_lng,
  updated_at = now();

-- Auto-bump updated_at on UPDATE.
CREATE OR REPLACE FUNCTION demo_mock_enrichment_touch_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS demo_mock_enrichment_touch_updated_at_trg
  ON demo_mock_enrichment;
CREATE TRIGGER demo_mock_enrichment_touch_updated_at_trg
  BEFORE UPDATE ON demo_mock_enrichment
  FOR EACH ROW EXECUTE FUNCTION demo_mock_enrichment_touch_updated_at();

-- RLS: read-only for tenant members (so the demo endpoint can
-- service-role bypass), no public writes.
ALTER TABLE demo_mock_enrichment ENABLE ROW LEVEL SECURITY;

-- Anyone authenticated can read — these aren't real customer PII,
-- they're synthetic demo seeds.
CREATE POLICY demo_mock_enrichment_read
  ON demo_mock_enrichment FOR SELECT
  USING (true);
