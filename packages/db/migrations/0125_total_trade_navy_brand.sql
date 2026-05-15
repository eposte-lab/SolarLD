-- 0125_total_trade_navy_brand.sql
--
-- Coerenza branding Total Trade.
--
-- 1) brand_primary_color del tenant: dal placeholder teal #0F766E al
--    vero navy Total Trade #183054 (estratto dal logo). Così portale
--    lead, dashboard e pagina /settings/branding mostrano lo stesso
--    colore usato nelle email.
--
-- 2) Template email del tenant: i colori navy/teal erano scritti a
--    mano nell'HTML. Vengono sostituiti con la variabile Jinja
--    {{ brand_primary_color }}, così le impostazioni diventano la
--    fonte unica — un domani basta cambiare il colore in
--    /settings/branding e tutte le email si aggiornano.
--    (render_template_for_lead ora espone brand_primary_color.)

UPDATE tenants
SET brand_primary_color = '#183054',
    updated_at = now()
WHERE id = 'df08df04-4c90-4613-b21e-80879fc958d1';

UPDATE email_templates
SET html = regexp_replace(
      regexp_replace(
        regexp_replace(html, '#0b1f4d', '{{ brand_primary_color }}', 'gi'),
        '#1e3a8a', '{{ brand_primary_color }}', 'gi'),
      '#0f766e', '{{ brand_primary_color }}', 'gi'),
    updated_at = now()
WHERE tenant_id = 'df08df04-4c90-4613-b21e-80879fc958d1'
  AND (
    html ~* '#0b1f4d' OR html ~* '#1e3a8a' OR html ~* '#0f766e'
  );
