-- ============================================================
-- 0014 — ATECO ↔ Google Places types mapping (Sprint 9)
-- ============================================================
-- Seed table that maps an Italian ATECO code (or rollup group) to
-- the set of Google Places `types` the Hunter should query when
-- looking for that kind of business.
--
-- The onboarding wizard shows Italian human-readable labels
-- (`ateco_label`) grouped by `wizard_group`. When the installer
-- checks a row, we compute:
--   config.place_type_whitelist = UNION(rows.google_types)
--   config.ateco_whitelist      = rows.ateco_code (for Tier 2)
--   config.place_type_priority  = seed of rows.priority_hint
--
-- Coverage is biased toward B2B categories with strong Google
-- Places presence in Italy (retail, HoReCa, automotive, healthcare,
-- professional offices, logistics). Pure manufacturing coverage is
-- intentionally thin — Google Places doesn't index factories well;
-- those installers should stay on `opportunistic` mode.

CREATE TABLE IF NOT EXISTS ateco_google_types (
  ateco_code       TEXT PRIMARY KEY,
  ateco_label      TEXT NOT NULL,
  wizard_group     TEXT NOT NULL,
    -- 'retail_gdo' | 'horeca' | 'automotive' | 'logistics'
    -- | 'healthcare' | 'education' | 'personal_services'
    -- | 'professional_offices' | 'industry_light'
  google_types     TEXT[] NOT NULL,
  target_segment   TEXT NOT NULL DEFAULT 'b2b'
    CHECK (target_segment IN ('b2b', 'b2c', 'mixed')),
  priority_hint    INT NOT NULL DEFAULT 5
    CHECK (priority_hint BETWEEN 1 AND 10),
  notes            TEXT
);

CREATE INDEX idx_ateco_google_group ON ateco_google_types(wizard_group);

-- Global read access — shared reference data like ateco_consumption_profiles.
ALTER TABLE ateco_google_types ENABLE ROW LEVEL SECURITY;
CREATE POLICY ateco_google_read_all ON ateco_google_types
  FOR SELECT USING (true);

-- ============================================================
-- SEED — top Italian B2B categories with good Places coverage
-- ============================================================

-- -----------------------------------------------------------
-- Retail / GDO (highest priority — large roofs + high kWh)
-- -----------------------------------------------------------
INSERT INTO ateco_google_types (ateco_code, ateco_label, wizard_group, google_types, priority_hint) VALUES
  ('47.11', 'Supermercati e ipermercati',           'retail_gdo', ARRAY['supermarket','grocery_or_supermarket'],         10),
  ('47.19', 'Grandi magazzini',                     'retail_gdo', ARRAY['department_store','shopping_mall'],              9),
  ('47.21', 'Frutta e verdura',                     'retail_gdo', ARRAY['store'],                                         5),
  ('47.24', 'Panetterie e pasticcerie',             'retail_gdo', ARRAY['bakery'],                                        6),
  ('47.30', 'Carburanti per autotrazione',          'retail_gdo', ARRAY['gas_station'],                                   7),
  ('47.41', 'Computer e periferiche',               'retail_gdo', ARRAY['electronics_store'],                             6),
  ('47.52', 'Ferramenta, vernici, vetro',           'retail_gdo', ARRAY['hardware_store'],                                7),
  ('47.54', 'Elettrodomestici',                     'retail_gdo', ARRAY['home_goods_store','electronics_store'],          6),
  ('47.59', 'Mobili e articoli per la casa',        'retail_gdo', ARRAY['furniture_store','home_goods_store'],            7),
  ('47.71', 'Abbigliamento',                        'retail_gdo', ARRAY['clothing_store'],                                5),
  ('47.72', 'Calzature e pelletterie',              'retail_gdo', ARRAY['shoe_store'],                                    5),
  ('47.73', 'Farmacie',                             'retail_gdo', ARRAY['pharmacy','drugstore'],                          7),
  ('47.76', 'Fiori, piante, animali domestici',     'retail_gdo', ARRAY['florist','pet_store'],                           4),
  ('47.77', 'Gioiellerie, orologerie',              'retail_gdo', ARRAY['jewelry_store'],                                 4);

-- -----------------------------------------------------------
-- HoReCa — good Places coverage
-- -----------------------------------------------------------
INSERT INTO ateco_google_types (ateco_code, ateco_label, wizard_group, google_types, priority_hint) VALUES
  ('55.10', 'Alberghi e strutture ricettive',       'horeca',     ARRAY['lodging'],                                       8),
  ('56.10', 'Ristoranti',                           'horeca',     ARRAY['restaurant','meal_takeaway'],                    6),
  ('56.30', 'Bar e caffetterie',                    'horeca',     ARRAY['cafe','bar'],                                    4);

-- -----------------------------------------------------------
-- Automotive
-- -----------------------------------------------------------
INSERT INTO ateco_google_types (ateco_code, ateco_label, wizard_group, google_types, priority_hint) VALUES
  ('45.11', 'Commercio di autoveicoli',             'automotive', ARRAY['car_dealer'],                                    8),
  ('45.20', 'Riparazione e manutenzione auto',      'automotive', ARRAY['car_repair','car_wash'],                         7),
  ('45.32', 'Commercio ricambi auto',               'automotive', ARRAY['car_repair'],                                    5);

-- -----------------------------------------------------------
-- Logistics & storage — massive roofs when available
-- -----------------------------------------------------------
INSERT INTO ateco_google_types (ateco_code, ateco_label, wizard_group, google_types, priority_hint) VALUES
  ('49.41', 'Trasporto merci su strada',            'logistics',  ARRAY['moving_company'],                                7),
  ('52.10', 'Magazzinaggio e stoccaggio',           'logistics',  ARRAY['storage'],                                       9);

-- -----------------------------------------------------------
-- Healthcare
-- -----------------------------------------------------------
INSERT INTO ateco_google_types (ateco_code, ateco_label, wizard_group, google_types, priority_hint) VALUES
  ('86.10', 'Ospedali e cliniche',                  'healthcare', ARRAY['hospital'],                                      9),
  ('86.21', 'Medici generici',                      'healthcare', ARRAY['doctor'],                                        4),
  ('86.23', 'Dentisti',                             'healthcare', ARRAY['dentist'],                                       5),
  ('75.00', 'Veterinari',                           'healthcare', ARRAY['veterinary_care'],                               4);

-- -----------------------------------------------------------
-- Education (public tenders possible)
-- -----------------------------------------------------------
INSERT INTO ateco_google_types (ateco_code, ateco_label, wizard_group, google_types, priority_hint) VALUES
  ('85.20', 'Istruzione primaria',                  'education',  ARRAY['primary_school','school'],                       6),
  ('85.31', 'Istruzione secondaria',                'education',  ARRAY['secondary_school','school'],                     7),
  ('85.41', 'Università e post-diploma',            'education',  ARRAY['university'],                                    8);

-- -----------------------------------------------------------
-- Personal services
-- -----------------------------------------------------------
INSERT INTO ateco_google_types (ateco_code, ateco_label, wizard_group, google_types, priority_hint) VALUES
  ('93.13', 'Palestre e attività sportive',         'personal_services', ARRAY['gym'],                                    6),
  ('96.02', 'Parrucchieri, estetica',               'personal_services', ARRAY['hair_care','beauty_salon'],               3),
  ('96.04', 'Servizi benessere e spa',              'personal_services', ARRAY['spa'],                                    5);

-- -----------------------------------------------------------
-- Professional offices (often own the building)
-- -----------------------------------------------------------
INSERT INTO ateco_google_types (ateco_code, ateco_label, wizard_group, google_types, priority_hint) VALUES
  ('68.31', 'Agenzie immobiliari',                  'professional_offices', ARRAY['real_estate_agency'],                  4),
  ('69.10', 'Studi legali',                         'professional_offices', ARRAY['lawyer'],                              4),
  ('69.20', 'Studi di contabilità',                 'professional_offices', ARRAY['accounting'],                          4),
  ('64.19', 'Banche',                               'professional_offices', ARRAY['bank'],                                3);

-- -----------------------------------------------------------
-- Light industry / manufacturing (weaker Places coverage)
-- Establishments with Google Places presence tend to be
-- consumer-facing food producers.
-- -----------------------------------------------------------
INSERT INTO ateco_google_types (ateco_code, ateco_label, wizard_group, google_types, priority_hint, notes) VALUES
  ('10.71', 'Produzione pane e dolci',              'industry_light', ARRAY['bakery'],              6,
   'Overlap con retail 47.24 — Places non distingue bene produzione vs vendita.'),
  ('10.51', 'Industria lattiero-casearia',          'industry_light', ARRAY['food','establishment'], 5,
   'Copertura Places limitata; fallback su establishment + filtro nome.'),
  ('25.11', 'Carpenteria metallica',                'industry_light', ARRAY['establishment'],       4,
   'Places non ha una type dedicata — consigliato mode opportunistic.');
