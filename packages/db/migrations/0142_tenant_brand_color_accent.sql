-- 0142_tenant_brand_color_accent.sql
--
-- Colore accento brand per-tenant, usato nei template email (CTA, badge
-- "€ 0" EPC, bordi delle schede metriche, valori evidenziati). Finora il
-- template `outreach_solarld_premium` ripiegava SEMPRE sul default
-- hardcoded `#F4A300` (oro/arancione) perché la colonna non esisteva e
-- `tenant_row.get("brand_color_accent")` tornava None.
--
-- Aggiungendola, ogni tenant può avere l'accento coerente col proprio
-- logo. Nullable: i tenant senza valore continuano a ricadere sull'oro
-- via il filtro Jinja `| default('#F4A300', true)`.
--
-- Validazione leggera: se valorizzato, deve essere un colore hex
-- (#RGB o #RRGGBB). Niente CHECK rigido su tutti i formati CSS — il
-- template lo inietta solo dentro `color:`/`background-color:`.

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS brand_color_accent TEXT
  CHECK (brand_color_accent IS NULL OR brand_color_accent ~ '^#[0-9A-Fa-f]{3}([0-9A-Fa-f]{3})?$');

COMMENT ON COLUMN tenants.brand_color_accent IS
  'Accento brand (hex) per i template email. NULL → fallback #F4A300.';
