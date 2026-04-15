-- Minimal ATECO seed — a realistic subset to start scoring.
-- Production should load full ATECO 2007 table.

INSERT INTO ateco_consumption_profiles (ateco_code, description, avg_yearly_kwh_per_employee, avg_yearly_kwh_per_sqm, energy_intensity_tier) VALUES
  ('10.00', 'Industrie alimentari', 12000, 220, 'high'),
  ('11.00', 'Industria delle bevande', 15000, 250, 'high'),
  ('13.00', 'Industria tessile', 10000, 180, 'high'),
  ('14.00', 'Confezione di articoli di abbigliamento', 3500, 80, 'medium'),
  ('20.00', 'Fabbricazione di prodotti chimici', 25000, 300, 'high'),
  ('22.00', 'Fabbricazione articoli in gomma e plastica', 18000, 220, 'high'),
  ('23.00', 'Fabbricazione altri prodotti minerali non metalliferi', 20000, 280, 'high'),
  ('24.00', 'Metallurgia', 45000, 400, 'high'),
  ('25.00', 'Fabbricazione prodotti in metallo', 12000, 180, 'high'),
  ('27.00', 'Fabbricazione apparecchiature elettriche', 8000, 120, 'medium'),
  ('28.00', 'Fabbricazione di macchinari e apparecchiature', 10000, 150, 'medium'),
  ('29.00', 'Fabbricazione di autoveicoli', 15000, 200, 'high'),
  ('41.00', 'Costruzione di edifici', 2000, 40, 'low'),
  ('45.00', 'Commercio e riparazione autoveicoli', 3000, 60, 'low'),
  ('46.00', 'Commercio all''ingrosso', 2500, 50, 'low'),
  ('47.00', 'Commercio al dettaglio', 2500, 90, 'medium'),
  ('49.00', 'Trasporto terrestre', 3500, 50, 'low'),
  ('52.00', 'Magazzinaggio e supporto ai trasporti', 2000, 35, 'low'),
  ('55.00', 'Alloggio', 5000, 120, 'medium'),
  ('56.00', 'Attività ristorazione', 4000, 180, 'medium'),
  ('62.00', 'Produzione software, consulenza informatica', 3500, 70, 'low'),
  ('69.00', 'Attività legali e contabilità', 2500, 55, 'low'),
  ('85.00', 'Istruzione', 2000, 45, 'low'),
  ('86.00', 'Assistenza sanitaria', 4500, 120, 'medium'),
  ('90.00', 'Attività creative, artistiche e intrattenimento', 2500, 60, 'low')
ON CONFLICT (ateco_code) DO NOTHING;
