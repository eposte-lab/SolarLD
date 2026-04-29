-- ============================================================
-- Demo tenant — decision_maker_phone backfill
-- ============================================================
--
-- Why this is in `seed/` and not `migrations/`
--   The 12 leads on the demo tenant
--   (`df08df04-4c90-4613-b21e-80879fc958d1`) were created via the
--   Atoka discovery loop, but at the time we hadn't yet added the
--   `phone` field to `AtokaProfile`, so their `subjects.decision_maker_phone`
--   are NULL. The phones below are realistic Italian numbers
--   (mix mobile / fisso) chosen to LOOK plausible against the
--   business names — they are NOT real publicly listed numbers
--   and must NEVER be dialled. They exist purely so the demo
--   dashboard does not show a `—` in the Telefono row.
--
-- Idempotent
--   The `WHERE` clauses gate by both tenant and the current NULL
--   state of the column, so re-running this script is a no-op
--   once the demo phones have been populated. Safe to apply
--   from the Supabase SQL editor or via the MCP.
--
-- Source provenance
--   `decision_maker_phone_source = 'manual'` — these were filled
--   by an operator (not Atoka, not scraped). The UI will show the
--   "Manuale" badge so the demo viewer can see we acknowledge it.
--
-- After Fase 2 lands (the customer-facing "Avvia test pipeline"
-- CTA), any new leads created by the demo user will get phones
-- populated automatically by the live L2/email_extraction path
-- and won't need manual seeding like this.
-- ============================================================

DO $$
DECLARE
  demo_tenant CONSTANT uuid := 'df08df04-4c90-4613-b21e-80879fc958d1';
  -- Realistic Italian phones: half mobili (3XX), half fissi (0X).
  -- Spread the distribution intentionally so the demo screenshot
  -- doesn't look templated.
  phones TEXT[] := ARRAY[
    '+39 02 89712340',   -- fisso Milano
    '+39 333 7421955',   -- mobile TIM
    '+39 049 8761234',   -- fisso Padova
    '+39 348 5510077',   -- mobile WindTre
    '+39 011 5630188',   -- fisso Torino
    '+39 366 2840193',   -- mobile Vodafone
    '+39 045 8073322',   -- fisso Verona
    '+39 339 7102544',   -- mobile TIM
    '+39 030 3387465',   -- fisso Brescia
    '+39 320 4471809',   -- mobile WindTre
    '+39 051 6450977',   -- fisso Bologna
    '+39 347 9183266'    -- mobile TIM
  ];
  rec RECORD;
  idx INT := 1;
BEGIN
  -- Iterate the demo tenant's subjects in a stable order (created_at,
  -- then id) so re-runs assign the same phone to the same row.
  FOR rec IN
    SELECT id
      FROM subjects
     WHERE tenant_id = demo_tenant
       AND decision_maker_phone IS NULL
     ORDER BY created_at NULLS LAST, id
  LOOP
    EXIT WHEN idx > array_length(phones, 1);
    UPDATE subjects
       SET decision_maker_phone = phones[idx],
           decision_maker_phone_source = 'manual'
     WHERE id = rec.id;
    idx := idx + 1;
  END LOOP;
END $$;
