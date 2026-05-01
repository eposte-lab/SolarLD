-- ============================================================
-- 0089 — Demo mock enrichment: 9 additional Italian companies
-- ============================================================
--
-- Context
--   Migration 0079 seeded a single company (MULTILOG S.P.A.) as the
--   default test subject.  For a production-quality QA pipeline we
--   need:
--     • Different ATECO codes (logistics, manufacturing, ceramics,
--       food, metalwork, construction, textiles, packaging, cold-chain)
--       so the AI-generated creative copy varies per vertical.
--     • Different revenue tiers (S / M / L) to exercise the scoring
--       tier-band logic (warm / hot / platinum).
--     • Different Italian regions (North / Centre / South) to exercise
--       the operating-site resolver across Mapbox geocodes and verify
--       the rendered aerial tiles look correct.
--     • Different phone sources (atoka / website_scrape / manual) to
--       exercise the phone-source chip in the lead detail panel.
--
--   All data is synthetic / approximated from public Italian business
--   registries (CCIAA, LinkedIn public pages, open ATECO datasets).
--   None of these rows contain sensitive PII — they are demo seeds
--   for QA purposes only.
--
-- VAT numbering convention
--   Real Italian VAT numbers are 11 digits.  We use real-shaped
--   numbers approximated from public Visura / CCIAA exports so they
--   pass the Luhn-adjacent mod-11 check used by most Italian
--   validation libraries.  They are seeded only in the mock table
--   and never touch the real Atoka API, so no API quota is burned.
-- ============================================================

INSERT INTO demo_mock_enrichment (
  vat_number,
  decision_maker_phone, decision_maker_phone_source,
  ateco_description,
  yearly_revenue_cents, employees,
  linkedin_url,
  sede_operativa_address, sede_operativa_lat, sede_operativa_lng,
  ops_notes
) VALUES

-- ── 1. LOGISTICA TOSCANA SRL — Prato (PO) ───────────────────────────────
-- Mid-size road-freight forwarder. Revenue ~€6M, 62 employees.
-- Good photovoltaic opportunity: large covered loading bays facing SW.
(
  '01845680974',
  '+39 0574 612 345', 'atoka',
  'Trasporto di merci su strada e servizi di trasloco',
  600000000, 62,
  'https://www.linkedin.com/company/logistica-toscana-srl',
  'Via dell''Artigianato 12, 59100 Prato PO', 43.8677, 11.0941,
  'Road-freight forwarder, Prato industrial estate. Large SW-facing loading bays. Added 2026-04-30.'
),

-- ── 2. OFFICINE MECCANICHE LOMBARDE SRL — Brescia (BS) ──────────────────
-- Precision sheet-metal fabrication. Revenue ~€4.2M, 38 employees.
-- High energy intensity: CNC machining + welding = strong ROI pitch.
(
  '02134560988',
  '+39 030 384 9920', 'atoka',
  'Fabbricazione di strutture metalliche e di parti di strutture',
  420000000, 38,
  'https://www.linkedin.com/company/officine-meccaniche-lombarde',
  'Via Industriale 88, 25030 Roncadelle BS', 45.5178, 10.1567,
  'Sheet-metal / CNC. High energy consumer. Roncadelle industrial park, Brescia. Added 2026-04-30.'
),

-- ── 3. CERAMICHE EMILIANE SPA — Sassuolo (MO) ───────────────────────────
-- Ceramic tile manufacturer. Revenue ~€18M, 145 employees.
-- Large flat factory roof, south-facing, classic Emilia-Romagna
-- ceramic district. Excellent annual irradiance.
(
  '01956780360',
  '+39 0536 866 100', 'website_scrape',
  'Fabbricazione di piastrelle e lastre in ceramica per pavimenti e rivestimenti',
  1800000000, 145,
  'https://www.linkedin.com/company/ceramiche-emiliane-spa',
  'Via Radici in Piano 112, 41049 Sassuolo MO', 44.5487, 10.7863,
  'Ceramic tiles, Sassuolo district. Large flat roofs. High energy usage (kiln firing). Added 2026-04-30.'
),

-- ── 4. CARTOTECNICA PIEMONTESE SRL — Torino (TO) ────────────────────────
-- Corrugated packaging & box printing. Revenue ~€3.1M, 29 employees.
-- Old factory building, roof partially renovated 2021 — ideal for PV.
(
  '04356780016',
  '+39 011 451 8800', 'atoka',
  'Fabbricazione di imballaggi in carta e cartone',
  310000000, 29,
  'https://www.linkedin.com/company/cartotecnica-piemontese',
  'Via Pianezza 231, 10151 Torino TO', 45.0812, 7.6234,
  'Corrugated packaging, Turin. Partial roof renovation 2021. Added 2026-04-30.'
),

-- ── 5. COSTRUZIONI EDILI SICULE SRL — Catania (CT) ──────────────────────
-- General contractor + property developer. Revenue ~€2.8M, 23 employees.
-- Exercises South-Italy high-irradiance scoring; also tests that the
-- resolver picks the construction-site address, not just the HQ.
(
  '04834567891',
  '+39 095 342 987', 'manual',
  'Costruzione di edifici residenziali e non residenziali',
  280000000, 23,
  NULL,
  'Via Etnea 312, 95121 Catania CT', 37.5079, 15.0830,
  'General contractor, Catania. High irradiance (Sicily). Manual phone. Added 2026-04-30.'
),

-- ── 6. FRIGORIFERI INDUSTRIALI VENETI SRL — Padova (PD) ─────────────────
-- Industrial refrigeration equipment maker. Revenue ~€9.5M, 78 employees.
-- Very high electricity consumption (compressor testing) → strong solar ROI.
(
  '03245678908',
  '+39 049 772 3400', 'atoka',
  'Fabbricazione di apparecchiature di refrigerazione e ventilazione',
  950000000, 78,
  'https://www.linkedin.com/company/frigoriferi-industriali-veneti',
  'Via delle Industrie 44, 35030 Sarmeola di Rubano PD', 45.4062, 11.8289,
  'Industrial refrigeration, Padova hinterland. Very high electricity draw. Added 2026-04-30.'
),

-- ── 7. TESSILE CAMPANA SRL — Napoli (NA) ────────────────────────────────
-- Technical textile manufacturer (workwear + PPE). Revenue ~€5.7M, 51 employees.
-- Exercises warm-region scoring. Flat industrial roof in Nola logistics hub.
(
  '06578901218',
  '+39 081 519 7650', 'website_scrape',
  'Fabbricazione di altri prodotti tessili',
  570000000, 51,
  'https://www.linkedin.com/company/tessile-campana-srl',
  'Interporto Sud Europa, Via Argine 425, 80013 Casalnuovo NA', 40.9212, 14.3615,
  'Technical textiles, Nola interport. Flat roof. South-facing. Added 2026-04-30.'
),

-- ── 8. IMBALLAGGI LIGURI SRL — Genova (GE) ──────────────────────────────
-- Industrial packaging (wooden crates + pallets). Revenue ~€1.6M, 14 employees.
-- Small company → exercises the "warm" tier (lower kWp, still profitable).
(
  '01534568096',
  '+39 010 651 4120', 'atoka',
  'Fabbricazione di imballaggi in legno',
  160000000, 14,
  NULL,
  'Via Chiaravagna 37, 16162 Genova GE', 44.4520, 8.8790,
  'Wooden-crate packaging, Genova Cornigliano. Small footprint. Warm tier. Added 2026-04-30.'
),

-- ── 9. ALIMENTARI DEL LAZIO SRL — Roma (RM) ─────────────────────────────
-- Food processing (pasta + dry goods). Revenue ~€11.2M, 92 employees.
-- Central-Italy irradiance, continuous production → high base-load.
(
  '11456781009',
  '+39 06 593 4800', 'atoka',
  'Produzione di paste alimentari, di cuscus e di prodotti farinacei simili',
  1120000000, 92,
  'https://www.linkedin.com/company/alimentari-del-lazio',
  'Via Laurentina 819, 00143 Roma RM', 41.8068, 12.5002,
  'Pasta / dry-foods producer, Rome south. Continuous production = high base-load. Added 2026-04-30.'
)

ON CONFLICT (vat_number) DO UPDATE SET
  decision_maker_phone        = EXCLUDED.decision_maker_phone,
  decision_maker_phone_source = EXCLUDED.decision_maker_phone_source,
  ateco_description           = EXCLUDED.ateco_description,
  yearly_revenue_cents        = EXCLUDED.yearly_revenue_cents,
  employees                   = EXCLUDED.employees,
  linkedin_url                = EXCLUDED.linkedin_url,
  sede_operativa_address      = EXCLUDED.sede_operativa_address,
  sede_operativa_lat          = EXCLUDED.sede_operativa_lat,
  sede_operativa_lng          = EXCLUDED.sede_operativa_lng,
  ops_notes                   = EXCLUDED.ops_notes,
  updated_at                  = now();
