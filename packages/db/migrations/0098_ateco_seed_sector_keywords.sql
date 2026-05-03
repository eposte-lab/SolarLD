-- ============================================================
-- 0098 — ateco_google_types: seed sector keywords + new wizard_groups
-- ============================================================
-- Two-part seed:
--   1) UPDATE existing rows (industry_light, retail_gdo, horeca,
--      logistics, healthcare, automotive, education,
--      personal_services, professional_offices) with the new
--      sector-aware columns added in 0097.
--   2) INSERT new ATECO codes for wizard_groups that didn't exist:
--      industry_heavy, food_production, hospitality_large,
--      hospitality_food_service, agricultural_intensive,
--      gdo_retail_anchor.
--
-- Source: addendum PRD "Smart Logic Target ATECO → Aree Geografiche"
-- mapped onto existing wizard_group taxonomy.
--
-- See plan: shimmying-painting-backus.md, Sprint A.2.

-- ============================================================
-- PART 1 — UPDATE existing wizard_groups with sector keywords
-- ============================================================

-- industry_light: tessile, plastica, carta, stampa, alimentare retail-facing
UPDATE ateco_google_types SET
  osm_landuse_hints     = '[{"landuse":"industrial","weight":0.8},{"landuse":"commercial","weight":0.4}]'::jsonb,
  osm_additional_tags   = '[]'::jsonb,
  places_keywords       = ARRAY['tessitura','abbigliamento produzione','lavorazione plastica','tipografia industriale','produzione carta','laboratorio artigianale'],
  places_excluded_types = ARRAY['clothing_store','bookstore'],
  site_signal_keywords  = ARRAY['capannone','laboratorio','stabilimento','produzione','industriale','officina'],
  min_zone_area_m2      = 3000,
  search_radius_m       = 1500,
  typical_kwp_range_min = 60,
  typical_kwp_range_max = 200
WHERE wizard_group = 'industry_light';

-- retail_gdo: ipermercati, centri commerciali, grossisti
UPDATE ateco_google_types SET
  osm_landuse_hints     = '[{"landuse":"retail","weight":1.0},{"landuse":"commercial","weight":0.8}]'::jsonb,
  osm_additional_tags   = '[{"shop":"mall","weight":1.0},{"shop":"supermarket","weight":0.9},{"building":"retail","weight":0.8}]'::jsonb,
  places_keywords       = ARRAY['ipermercato','supermercato grande','centro commerciale','outlet','cash and carry','grossista alimentare'],
  places_excluded_types = ARRAY['convenience_store'],
  site_signal_keywords  = ARRAY['ipermercato','supermercato','centro commerciale','vendita','negozio','retail'],
  min_zone_area_m2      = 5000,
  search_radius_m       = 1000,
  typical_kwp_range_min = 100,
  typical_kwp_range_max = 500
WHERE wizard_group = 'retail_gdo';

-- horeca: ristoranti + bar (small footprint, urban)
UPDATE ateco_google_types SET
  osm_landuse_hints     = '[{"landuse":"commercial","weight":0.5}]'::jsonb,
  osm_additional_tags   = '[{"amenity":"restaurant","weight":0.6},{"amenity":"cafe","weight":0.4}]'::jsonb,
  places_keywords       = ARRAY['ristorante','bar','caffetteria','pizzeria','trattoria','osteria'],
  places_excluded_types = ARRAY[]::TEXT[],
  site_signal_keywords  = ARRAY['ristorante','bar','caffetteria','menù','prenotazione'],
  min_zone_area_m2      = 200,
  search_radius_m       = 500,
  typical_kwp_range_min = 10,
  typical_kwp_range_max = 50
WHERE wizard_group = 'horeca';

-- logistics: magazzini, hub, spedizioni
UPDATE ateco_google_types SET
  osm_landuse_hints     = '[{"landuse":"industrial","weight":1.0},{"landuse":"commercial","weight":0.7}]'::jsonb,
  osm_additional_tags   = '[{"building":"warehouse","weight":1.0},{"industrial":"warehouse","weight":1.0},{"highway":"motorway_junction","weight":0.4}]'::jsonb,
  places_keywords       = ARRAY['centro logistico','magazzino industriale','hub logistico','spedizioni industriali','deposito merci','centro distribuzione'],
  places_excluded_types = ARRAY['storage','self_storage'],
  site_signal_keywords  = ARRAY['logistica','magazzino','spedizione','stoccaggio','distribuzione','warehouse','hub'],
  min_zone_area_m2      = 8000,
  search_radius_m       = 2500,
  typical_kwp_range_min = 150,
  typical_kwp_range_max = 800
WHERE wizard_group = 'logistics';

-- healthcare: ospedali + cliniche generaliste (esistenti)
UPDATE ateco_google_types SET
  osm_landuse_hints     = '[]'::jsonb,
  osm_additional_tags   = '[{"amenity":"hospital","weight":1.0},{"amenity":"clinic","weight":0.9},{"healthcare":"hospital","weight":1.0}]'::jsonb,
  places_keywords       = ARRAY['ospedale','clinica','poliambulatorio','centro medico','casa di cura'],
  places_excluded_types = ARRAY[]::TEXT[],
  site_signal_keywords  = ARRAY['ospedale','clinica','reparto','ambulatorio','sanitario','medico'],
  min_zone_area_m2      = 1500,
  search_radius_m       = 800,
  typical_kwp_range_min = 80,
  typical_kwp_range_max = 300
WHERE wizard_group = 'healthcare';

-- automotive: concessionari, officine grandi
UPDATE ateco_google_types SET
  osm_landuse_hints     = '[{"landuse":"commercial","weight":0.7},{"landuse":"industrial","weight":0.5}]'::jsonb,
  osm_additional_tags   = '[{"shop":"car","weight":1.0},{"amenity":"car_rental","weight":0.6}]'::jsonb,
  places_keywords       = ARRAY['concessionaria auto','autosalone','autoriparazioni','carrozzeria','autodemolizione'],
  places_excluded_types = ARRAY['gas_station'],
  site_signal_keywords  = ARRAY['concessionaria','autosalone','officina','autoriparazioni','vendita auto'],
  min_zone_area_m2      = 1500,
  search_radius_m       = 1200,
  typical_kwp_range_min = 50,
  typical_kwp_range_max = 200
WHERE wizard_group = 'automotive';

-- education: scuole superiori, università
UPDATE ateco_google_types SET
  osm_landuse_hints     = '[]'::jsonb,
  osm_additional_tags   = '[{"amenity":"school","weight":0.9},{"amenity":"university","weight":1.0},{"building":"school","weight":0.9}]'::jsonb,
  places_keywords       = ARRAY['scuola','liceo','università','istituto tecnico','campus universitario'],
  places_excluded_types = ARRAY['preschool'],
  site_signal_keywords  = ARRAY['scuola','liceo','università','istituto','studenti','iscrizioni'],
  min_zone_area_m2      = 2000,
  search_radius_m       = 600,
  typical_kwp_range_min = 50,
  typical_kwp_range_max = 250
WHERE wizard_group = 'education';

-- personal_services: palestre, spa, parrucchieri
UPDATE ateco_google_types SET
  osm_landuse_hints     = '[]'::jsonb,
  osm_additional_tags   = '[{"leisure":"fitness_centre","weight":0.9},{"leisure":"sports_centre","weight":0.8}]'::jsonb,
  places_keywords       = ARRAY['palestra','centro fitness','spa','centro benessere','piscina coperta'],
  places_excluded_types = ARRAY[]::TEXT[],
  site_signal_keywords  = ARRAY['palestra','fitness','spa','wellness','benessere','piscina'],
  min_zone_area_m2      = 800,
  search_radius_m       = 800,
  typical_kwp_range_min = 20,
  typical_kwp_range_max = 100
WHERE wizard_group = 'personal_services';

-- professional_offices: studi, banche, immobiliari
UPDATE ateco_google_types SET
  osm_landuse_hints     = '[{"landuse":"commercial","weight":0.4}]'::jsonb,
  osm_additional_tags   = '[{"office":"yes","weight":0.5}]'::jsonb,
  places_keywords       = ARRAY['studio legale','studio notarile','agenzia immobiliare','banca','filiale bancaria'],
  places_excluded_types = ARRAY[]::TEXT[],
  site_signal_keywords  = ARRAY['studio','consulenza','professionale','servizi legali','agenzia'],
  min_zone_area_m2      = 200,
  search_radius_m       = 500,
  typical_kwp_range_min = 10,
  typical_kwp_range_max = 50
WHERE wizard_group = 'professional_offices';

-- ============================================================
-- PART 2 — INSERT new wizard_groups for the sector-aware funnel
-- ============================================================

-- ----------------------------------------------------------------
-- industry_heavy: metalmeccanico, fonderie, chimica pesante
-- (manufacturing_heavy in addendum)
-- ----------------------------------------------------------------
INSERT INTO ateco_google_types (
  ateco_code, ateco_label, wizard_group, google_types, target_segment, priority_hint, notes,
  osm_landuse_hints, osm_additional_tags, places_keywords, places_excluded_types,
  site_signal_keywords, min_zone_area_m2, search_radius_m,
  typical_kwp_range_min, typical_kwp_range_max
) VALUES
  ('24.10', 'Siderurgia',                     'industry_heavy', ARRAY['establishment'], 'b2b', 8,
   'Places copre poco — mode opportunistic + atoka discovery primario.',
   '[{"landuse":"industrial","weight":1.0}]'::jsonb,
   '[{"man_made":"works","weight":0.9},{"industrial":"factory","weight":0.9}]'::jsonb,
   ARRAY['acciaieria','siderurgia','fonderia','laminatoio'],
   ARRAY['car_repair','car_dealer'],
   ARRAY['siderurgia','acciaieria','fonderia','laminazione','metallurgia'],
   8000, 1500, 200, 1000),
  ('25.11', 'Carpenteria metallica',          'industry_heavy', ARRAY['establishment'], 'b2b', 7,
   NULL,
   '[{"landuse":"industrial","weight":1.0}]'::jsonb,
   '[{"man_made":"works","weight":0.9}]'::jsonb,
   ARRAY['carpenteria metallica','officina meccanica industriale','stabilimento metalmeccanico','stamperia industriale'],
   ARRAY['car_repair','car_dealer','gas_station'],
   ARRAY['capannone','stabilimento','metalmeccanico','officina','carpenteria','fabbrica'],
   5000, 1500, 100, 500),
  ('28.41', 'Macchine utensili',              'industry_heavy', ARRAY['establishment'], 'b2b', 7,
   NULL,
   '[{"landuse":"industrial","weight":1.0}]'::jsonb,
   '[{"man_made":"works","weight":0.9}]'::jsonb,
   ARRAY['macchine utensili','officina meccanica industriale'],
   ARRAY['car_repair'],
   ARRAY['macchine utensili','officina','industriale','stabilimento'],
   5000, 1500, 100, 400),
  ('20.11', 'Industria chimica pesante',      'industry_heavy', ARRAY['establishment'], 'b2b', 6,
   NULL,
   '[{"landuse":"industrial","weight":1.0}]'::jsonb,
   '[{"industrial":"chemical","weight":1.0}]'::jsonb,
   ARRAY['industria chimica','stabilimento chimico'],
   ARRAY['pharmacy','car_repair'],
   ARRAY['chimica','stabilimento chimico','sintesi','industria chimica'],
   8000, 1500, 200, 800)
ON CONFLICT (ateco_code) DO UPDATE SET
  wizard_group           = EXCLUDED.wizard_group,
  osm_landuse_hints      = EXCLUDED.osm_landuse_hints,
  osm_additional_tags    = EXCLUDED.osm_additional_tags,
  places_keywords        = EXCLUDED.places_keywords,
  places_excluded_types  = EXCLUDED.places_excluded_types,
  site_signal_keywords   = EXCLUDED.site_signal_keywords,
  min_zone_area_m2       = EXCLUDED.min_zone_area_m2,
  search_radius_m        = EXCLUDED.search_radius_m,
  typical_kwp_range_min  = EXCLUDED.typical_kwp_range_min,
  typical_kwp_range_max  = EXCLUDED.typical_kwp_range_max;

-- ----------------------------------------------------------------
-- food_production: industria alimentare e bevande
-- ----------------------------------------------------------------
INSERT INTO ateco_google_types (
  ateco_code, ateco_label, wizard_group, google_types, target_segment, priority_hint, notes,
  osm_landuse_hints, osm_additional_tags, places_keywords, places_excluded_types,
  site_signal_keywords, min_zone_area_m2, search_radius_m,
  typical_kwp_range_min, typical_kwp_range_max
) VALUES
  ('10.11', 'Lavorazione carne',              'food_production', ARRAY['establishment'], 'b2b', 8,
   NULL,
   '[{"landuse":"industrial","weight":1.0},{"landuse":"farmyard","weight":0.7}]'::jsonb,
   '[{"industrial":"food","weight":1.0}]'::jsonb,
   ARRAY['salumificio','lavorazione carne','prosciuttificio','macello industriale'],
   ARRAY['butcher_shop','restaurant'],
   ARRAY['salumificio','lavorazione carne','prosciuttificio','stabilimento alimentare'],
   3000, 2000, 100, 400),
  ('10.51', 'Lattiero-casearia',              'food_production', ARRAY['food','establishment'], 'b2b', 7,
   'Spostato da industry_light a food_production. Caseifici industriali grandi.',
   '[{"landuse":"industrial","weight":1.0},{"landuse":"farmyard","weight":0.7}]'::jsonb,
   '[{"industrial":"food","weight":1.0}]'::jsonb,
   ARRAY['caseificio industriale','stabilimento lattiero','lavorazione latte'],
   ARRAY['restaurant','grocery_or_supermarket'],
   ARRAY['caseificio','lattiero','formaggio','latticini','industria casearia'],
   3000, 2000, 100, 400),
  ('10.71', 'Produzione pane e dolci industriali', 'food_production', ARRAY['bakery','establishment'], 'b2b', 6,
   'Spostato da industry_light. Distinto dalle panetterie retail (47.24).',
   '[{"landuse":"industrial","weight":0.8}]'::jsonb,
   '[{"industrial":"food","weight":1.0}]'::jsonb,
   ARRAY['panificio industriale','produzione dolci industriale','biscottificio'],
   ARRAY['bakery'],
   ARRAY['panificio industriale','dolciario','biscottificio','produzione pane'],
   2000, 1500, 60, 250),
  ('10.91', 'Mangimi industriali',            'food_production', ARRAY['establishment'], 'b2b', 6,
   NULL,
   '[{"landuse":"industrial","weight":1.0},{"landuse":"farmyard","weight":0.6}]'::jsonb,
   '[{"industrial":"food","weight":1.0}]'::jsonb,
   ARRAY['mangimificio','produzione mangimi'],
   ARRAY[]::TEXT[],
   ARRAY['mangimi','mangimificio','zootecnia industriale'],
   3000, 2000, 100, 400),
  ('11.07', 'Bevande analcoliche e acque',    'food_production', ARRAY['establishment'], 'b2b', 6,
   NULL,
   '[{"landuse":"industrial","weight":1.0}]'::jsonb,
   '[{"industrial":"food","weight":1.0}]'::jsonb,
   ARRAY['imbottigliamento','stabilimento bevande','acque minerali'],
   ARRAY['liquor_store'],
   ARRAY['imbottigliamento','bevande','acqua minerale','stabilimento'],
   3000, 2000, 100, 400)
ON CONFLICT (ateco_code) DO UPDATE SET
  wizard_group           = EXCLUDED.wizard_group,
  notes                  = EXCLUDED.notes,
  osm_landuse_hints      = EXCLUDED.osm_landuse_hints,
  osm_additional_tags    = EXCLUDED.osm_additional_tags,
  places_keywords        = EXCLUDED.places_keywords,
  places_excluded_types  = EXCLUDED.places_excluded_types,
  site_signal_keywords   = EXCLUDED.site_signal_keywords,
  min_zone_area_m2       = EXCLUDED.min_zone_area_m2,
  search_radius_m        = EXCLUDED.search_radius_m,
  typical_kwp_range_min  = EXCLUDED.typical_kwp_range_min,
  typical_kwp_range_max  = EXCLUDED.typical_kwp_range_max;

-- ----------------------------------------------------------------
-- hospitality_large: hotel 4-5 stelle, resort
-- (split out from horeca)
-- ----------------------------------------------------------------
INSERT INTO ateco_google_types (
  ateco_code, ateco_label, wizard_group, google_types, target_segment, priority_hint, notes,
  osm_landuse_hints, osm_additional_tags, places_keywords, places_excluded_types,
  site_signal_keywords, min_zone_area_m2, search_radius_m,
  typical_kwp_range_min, typical_kwp_range_max
) VALUES
  ('55.10.10', 'Hotel grandi e resort',       'hospitality_large', ARRAY['lodging'], 'b2b', 8,
   'Subset di 55.10 — hotel ≥ 4 stelle e resort. Splittato per pricing premium.',
   '[{"landuse":"commercial","weight":0.5}]'::jsonb,
   '[{"tourism":"hotel","weight":1.0},{"tourism":"resort","weight":1.0},{"building":"hotel","weight":0.9}]'::jsonb,
   ARRAY['hotel 4 stelle','hotel 5 stelle','resort','hotel congressuale','hotel business'],
   ARRAY['bed_and_breakfast','campground','hostel'],
   ARRAY['hotel','resort','quattro stelle','cinque stelle','suite','spa','convention'],
   1000, 800, 60, 250)
ON CONFLICT (ateco_code) DO UPDATE SET
  wizard_group           = EXCLUDED.wizard_group,
  notes                  = EXCLUDED.notes,
  osm_landuse_hints      = EXCLUDED.osm_landuse_hints,
  osm_additional_tags    = EXCLUDED.osm_additional_tags,
  places_keywords        = EXCLUDED.places_keywords,
  places_excluded_types  = EXCLUDED.places_excluded_types,
  site_signal_keywords   = EXCLUDED.site_signal_keywords,
  min_zone_area_m2       = EXCLUDED.min_zone_area_m2,
  search_radius_m        = EXCLUDED.search_radius_m,
  typical_kwp_range_min  = EXCLUDED.typical_kwp_range_min,
  typical_kwp_range_max  = EXCLUDED.typical_kwp_range_max;

-- ----------------------------------------------------------------
-- hospitality_food_service: ristorazione collettiva, mense
-- (subset of horeca with industrial scale)
-- ----------------------------------------------------------------
INSERT INTO ateco_google_types (
  ateco_code, ateco_label, wizard_group, google_types, target_segment, priority_hint, notes,
  osm_landuse_hints, osm_additional_tags, places_keywords, places_excluded_types,
  site_signal_keywords, min_zone_area_m2, search_radius_m,
  typical_kwp_range_min, typical_kwp_range_max
) VALUES
  ('56.29', 'Mense e ristorazione collettiva', 'hospitality_food_service', ARRAY['restaurant','establishment'], 'b2b', 6,
   'Catering industriale e mense aziendali — distinto da 56.10 (ristoranti retail).',
   '[]'::jsonb,
   '[{"amenity":"restaurant","weight":0.4}]'::jsonb,
   ARRAY['catering industriale','mensa aziendale','ristorazione collettiva','catering ospedaliero'],
   ARRAY['cafe','bar'],
   ARRAY['catering','mensa','ristorazione collettiva','mense aziendali'],
   500, 500, 30, 120)
ON CONFLICT (ateco_code) DO UPDATE SET
  wizard_group           = EXCLUDED.wizard_group,
  notes                  = EXCLUDED.notes,
  osm_landuse_hints      = EXCLUDED.osm_landuse_hints,
  osm_additional_tags    = EXCLUDED.osm_additional_tags,
  places_keywords        = EXCLUDED.places_keywords,
  places_excluded_types  = EXCLUDED.places_excluded_types,
  site_signal_keywords   = EXCLUDED.site_signal_keywords,
  min_zone_area_m2       = EXCLUDED.min_zone_area_m2,
  search_radius_m        = EXCLUDED.search_radius_m,
  typical_kwp_range_min  = EXCLUDED.typical_kwp_range_min,
  typical_kwp_range_max  = EXCLUDED.typical_kwp_range_max;

-- ----------------------------------------------------------------
-- agricultural_intensive: allevamenti, agroindustria
-- ----------------------------------------------------------------
INSERT INTO ateco_google_types (
  ateco_code, ateco_label, wizard_group, google_types, target_segment, priority_hint, notes,
  osm_landuse_hints, osm_additional_tags, places_keywords, places_excluded_types,
  site_signal_keywords, min_zone_area_m2, search_radius_m,
  typical_kwp_range_min, typical_kwp_range_max
) VALUES
  ('01.41', 'Allevamento bovini da latte',    'agricultural_intensive', ARRAY['establishment'], 'b2b', 7,
   NULL,
   '[{"landuse":"farmyard","weight":1.0},{"landuse":"farmland","weight":0.6}]'::jsonb,
   '[{"building":"barn","weight":0.8},{"building":"farm","weight":0.8},{"agricultural":"yes","weight":0.9}]'::jsonb,
   ARRAY['allevamento bovini','azienda agricola intensiva','stalla industriale'],
   ARRAY[]::TEXT[],
   ARRAY['allevamento','stalla','azienda agricola','bovini','latteria','zootecnia'],
   3000, 2500, 60, 200),
  ('01.45', 'Allevamento ovini e caprini',    'agricultural_intensive', ARRAY['establishment'], 'b2b', 6,
   NULL,
   '[{"landuse":"farmyard","weight":1.0}]'::jsonb,
   '[{"building":"barn","weight":0.8}]'::jsonb,
   ARRAY['allevamento ovini','allevamento caprini','azienda agricola'],
   ARRAY[]::TEXT[],
   ARRAY['allevamento','ovini','caprini','azienda agricola'],
   2500, 2500, 50, 150),
  ('01.47', 'Allevamento avicolo',            'agricultural_intensive', ARRAY['establishment'], 'b2b', 7,
   NULL,
   '[{"landuse":"farmyard","weight":1.0}]'::jsonb,
   '[{"building":"barn","weight":0.8},{"agricultural":"yes","weight":0.9}]'::jsonb,
   ARRAY['allevamento avicolo','allevamento polli','allevamento intensivo'],
   ARRAY[]::TEXT[],
   ARRAY['allevamento','avicolo','polli','intensivo'],
   3000, 2500, 60, 200)
ON CONFLICT (ateco_code) DO UPDATE SET
  wizard_group           = EXCLUDED.wizard_group,
  osm_landuse_hints      = EXCLUDED.osm_landuse_hints,
  osm_additional_tags    = EXCLUDED.osm_additional_tags,
  places_keywords        = EXCLUDED.places_keywords,
  places_excluded_types  = EXCLUDED.places_excluded_types,
  site_signal_keywords   = EXCLUDED.site_signal_keywords,
  min_zone_area_m2       = EXCLUDED.min_zone_area_m2,
  search_radius_m        = EXCLUDED.search_radius_m,
  typical_kwp_range_min  = EXCLUDED.typical_kwp_range_min,
  typical_kwp_range_max  = EXCLUDED.typical_kwp_range_max;
