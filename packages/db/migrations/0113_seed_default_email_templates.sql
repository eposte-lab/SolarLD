-- 0113 — seed default email templates for all tenants.
--
-- Provides every tenant with three pre-configured templates ready to use
-- for `generic_outreach` campaigns (Trova aziende → amministratori di
-- condominio and similar B2B angles):
--
--   1. "Base professionale"           — clean default, single accent rail
--   2. "A/B Variante A — Diretto"     — stat-hero with −30% callout
--   3. "A/B Variante B — Conversazionale" — minimal serif, peer-to-peer
--
-- The two A/B variants share the same value proposition but differ in
-- tone and structure so operators can run a clean two-arm A/B test by
-- attaching variant A to one list and variant B to another.
--
-- All three templates include the four GDPR-required Jinja2 variables
-- (unsubscribe_url, tenant_legal_name, tenant_vat_number,
-- tenant_legal_address) so they pass the validator in
-- routes/email_templates.py.
--
-- Idempotency: the seed function skips templates whose `name` already
-- exists for the target tenant, so re-running the migration won't
-- clobber operator edits or create duplicates.

BEGIN;

CREATE OR REPLACE FUNCTION seed_default_email_templates_for_tenant(p_tenant_id UUID)
RETURNS void
LANGUAGE plpgsql
AS $func$
DECLARE
  v_html_base TEXT;
  v_html_var_a TEXT;
  v_html_var_b TEXT;
  v_text_base TEXT;
  v_text_var_a TEXT;
  v_text_var_b TEXT;
  v_vars_with_logo JSONB := '["brand_logo_url","business_name","greeting_name","hq_city","sender_first_name","tenant_legal_address","tenant_legal_name","tenant_name","tenant_vat_number","tracking_pixel_url","unsubscribe_url"]'::jsonb;
  v_vars_no_logo   JSONB := '["business_name","greeting_name","hq_city","sender_first_name","tenant_legal_address","tenant_legal_name","tenant_name","tenant_vat_number","tracking_pixel_url","unsubscribe_url"]'::jsonb;
BEGIN

  -- ── Template 1 — Base professionale ──────────────────────────────
  v_html_base := $html$<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>{{ business_name }}</title>
</head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2937;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:32px 12px;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,0.04);overflow:hidden;">
        {% if brand_logo_url %}
        <tr><td style="padding:28px 32px 0 32px;">
          <img src="{{ brand_logo_url }}" alt="{{ tenant_name }}" height="32" style="height:32px;width:auto;display:block;border:0;">
        </td></tr>
        {% endif %}
        <tr><td style="padding:24px 32px 8px 32px;">
          <div style="height:3px;width:42px;background:#0f766e;border-radius:2px;margin-bottom:18px;"></div>
          <h1 style="margin:0;font-size:22px;line-height:1.3;font-weight:700;color:#0f172a;letter-spacing:-0.01em;">Buongiorno {{ greeting_name }},</h1>
        </td></tr>
        <tr><td style="padding:18px 32px 0 32px;font-size:15px;line-height:1.65;color:#334155;">
          <p style="margin:0 0 14px 0;">Mi presento: sono {{ sender_first_name }} di {{ tenant_name }}. Mi occupo di soluzioni di efficientamento energetico per gli edifici gestiti da studi di amministrazione come {{ business_name }} a {{ hq_city }}.</p>
          <p style="margin:0 0 14px 0;">Aiutiamo gli amministratori a portare ai propri condomini un impianto fotovoltaico chiavi in mano — pratica, finanziamento e installazione gestiti interamente da noi — riducendo le spese condominiali ricorrenti senza esborso iniziale dei condomini.</p>
          <p style="margin:0 0 14px 0;">Se può essere utile, posso preparare una stima del risparmio per uno dei vostri immobili: bastano l'indirizzo e qualche dato di consumo.</p>
        </td></tr>
        <tr><td style="padding:18px 32px 28px 32px;">
          <a href="mailto:?subject=Richiesta%20stima%20fotovoltaico%20condominiale" style="display:inline-block;padding:11px 22px;background:#0f766e;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600;font-size:14px;">Richiedi una stima gratuita</a>
        </td></tr>
        <tr><td style="padding:0 32px 28px 32px;font-size:14px;line-height:1.6;color:#334155;">
          <p style="margin:0;">Resto a disposizione per qualsiasi domanda.</p>
          <p style="margin:8px 0 0 0;font-weight:600;color:#0f172a;">{{ sender_first_name }}<br><span style="font-weight:400;color:#64748b;">{{ tenant_name }}</span></p>
        </td></tr>
        <tr><td style="border-top:1px solid #e5e7eb;padding:18px 32px;background:#fafbfc;font-size:11px;line-height:1.6;color:#94a3b8;">
          {{ tenant_legal_name }} · P.IVA {{ tenant_vat_number }}<br>
          {{ tenant_legal_address }}<br>
          <a href="{{ unsubscribe_url }}" style="color:#94a3b8;text-decoration:underline;">Disiscriviti da queste comunicazioni</a>
        </td></tr>
      </table>
    </td></tr>
  </table>
  <img src="{{ tracking_pixel_url }}" alt="" width="1" height="1" style="display:none;width:1px;height:1px;border:0;">
</body>
</html>$html$;

  v_text_base := $txt$Buongiorno {{ greeting_name }},

Mi presento: sono {{ sender_first_name }} di {{ tenant_name }}. Mi occupo di soluzioni di efficientamento energetico per gli edifici gestiti da studi di amministrazione come {{ business_name }} a {{ hq_city }}.

Aiutiamo gli amministratori a portare ai propri condomini un impianto fotovoltaico chiavi in mano — pratica, finanziamento e installazione gestiti interamente da noi — riducendo le spese condominiali ricorrenti senza esborso iniziale dei condomini.

Se può essere utile, posso preparare una stima del risparmio per uno dei vostri immobili: bastano l'indirizzo e qualche dato di consumo.

Resto a disposizione,
{{ sender_first_name }}
{{ tenant_name }}

---
{{ tenant_legal_name }} · P.IVA {{ tenant_vat_number }}
{{ tenant_legal_address }}
Disiscriviti: {{ unsubscribe_url }}$txt$;

  -- ── Template 2 — A/B Variante A — Diretto / ROI ──────────────────
  v_html_var_a := $html$<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>{{ business_name }}</title>
</head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2937;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0f172a;padding:36px 12px;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,0.18);">
        <tr><td style="padding:36px 36px 28px 36px;text-align:center;background:linear-gradient(135deg,#0f766e 0%,#14b8a6 100%);">
          <p style="margin:0;font-size:11px;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:rgba(255,255,255,0.85);">Risparmio annuo stimato</p>
          <p style="margin:6px 0 0 0;font-size:64px;line-height:1;font-weight:800;color:#ffffff;letter-spacing:-0.04em;">−30%</p>
          <p style="margin:8px 0 0 0;font-size:13px;color:rgba(255,255,255,0.9);">in bolletta condominiale, senza esborso dei condomini</p>
        </td></tr>
        <tr><td style="padding:30px 36px 6px 36px;">
          <h2 style="margin:0;font-size:20px;line-height:1.35;font-weight:700;color:#0f172a;letter-spacing:-0.01em;">{{ greeting_name }}, ecco cosa cambia per i condomini di {{ business_name }}</h2>
        </td></tr>
        <tr><td style="padding:14px 36px 0 36px;font-size:15px;line-height:1.65;color:#334155;">
          <p style="margin:0 0 16px 0;">Sono {{ sender_first_name }} di {{ tenant_name }}. A {{ hq_city }} aiutiamo studi di amministrazione come il vostro a portare il fotovoltaico nei condomini gestiti, con tre impegni concreti:</p>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 16px 0;">
            <tr><td style="padding:6px 0;border-bottom:1px solid #f1f5f9;font-size:14px;color:#0f172a;"><span style="color:#0f766e;font-weight:700;">→</span>&nbsp;&nbsp;Zero anticipo per i condomini (finanziamento o cessione del credito)</td></tr>
            <tr><td style="padding:6px 0;border-bottom:1px solid #f1f5f9;font-size:14px;color:#0f172a;"><span style="color:#0f766e;font-weight:700;">→</span>&nbsp;&nbsp;Tutta la pratica gestita da noi (delibera, GSE, allacciamento)</td></tr>
            <tr><td style="padding:6px 0;font-size:14px;color:#0f172a;"><span style="color:#0f766e;font-weight:700;">→</span>&nbsp;&nbsp;Stima personalizzata in 48 ore per ogni edificio</td></tr>
          </table>
        </td></tr>
        <tr><td style="padding:18px 36px 32px 36px;text-align:center;">
          <a href="mailto:?subject=Richiesta%20stima%20fotovoltaico%20condominiale" style="display:inline-block;padding:13px 28px;background:#0f766e;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600;font-size:15px;">Calcola il risparmio del vostro primo condominio</a>
          <p style="margin:14px 0 0 0;font-size:12px;color:#64748b;">15 minuti, nessun impegno</p>
        </td></tr>
        <tr><td style="padding:18px 36px 24px 36px;font-size:14px;line-height:1.6;color:#334155;border-top:1px solid #e5e7eb;">
          <p style="margin:0;">Buona giornata,</p>
          <p style="margin:6px 0 0 0;font-weight:600;color:#0f172a;">{{ sender_first_name }} · {{ tenant_name }}</p>
        </td></tr>
        <tr><td style="border-top:1px solid #e5e7eb;padding:18px 36px;background:#fafbfc;font-size:11px;line-height:1.6;color:#94a3b8;">
          {{ tenant_legal_name }} · P.IVA {{ tenant_vat_number }}<br>
          {{ tenant_legal_address }}<br>
          <a href="{{ unsubscribe_url }}" style="color:#94a3b8;text-decoration:underline;">Disiscriviti da queste comunicazioni</a>
        </td></tr>
      </table>
    </td></tr>
  </table>
  <img src="{{ tracking_pixel_url }}" alt="" width="1" height="1" style="display:none;width:1px;height:1px;border:0;">
</body>
</html>$html$;

  v_text_var_a := $txt${{ greeting_name }}, ecco cosa cambia per i condomini di {{ business_name }}

— Risparmio annuo stimato: fino al 30% in bolletta condominiale, senza esborso dei condomini —

Sono {{ sender_first_name }} di {{ tenant_name }}. A {{ hq_city }} aiutiamo studi di amministrazione come il vostro a portare il fotovoltaico nei condomini gestiti, con tre impegni concreti:

→ Zero anticipo per i condomini (finanziamento o cessione del credito)
→ Tutta la pratica gestita da noi (delibera, GSE, allacciamento)
→ Stima personalizzata in 48 ore per ogni edificio

Calcola il risparmio del vostro primo condominio — 15 minuti, nessun impegno.

Buona giornata,
{{ sender_first_name }} · {{ tenant_name }}

---
{{ tenant_legal_name }} · P.IVA {{ tenant_vat_number }}
{{ tenant_legal_address }}
Disiscriviti: {{ unsubscribe_url }}$txt$;

  -- ── Template 3 — A/B Variante B — Conversazionale ────────────────
  v_html_var_b := $html$<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>{{ business_name }}</title>
</head>
<body style="margin:0;padding:0;background:#ffffff;font-family:Georgia,'Times New Roman',Times,serif;color:#1f2937;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:48px 16px;">
    <tr><td align="center">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">
        <tr><td style="padding:0 0 28px 0;font-size:17px;line-height:1.7;color:#1f2937;">
          <p style="margin:0 0 18px 0;">Buongiorno {{ greeting_name }},</p>
          <p style="margin:0 0 18px 0;">Le scrivo perché ho notato che {{ business_name }} amministra immobili nella zona di {{ hq_city }} e volevo condividere un'esperienza recente.</p>
          <p style="margin:0 0 18px 0;">Stiamo lavorando con altri studi di amministrazione condominiale qui intorno per installare impianti fotovoltaici sui tetti dei condomini gestiti. La parte che è piaciuta di più agli amministratori è che la pratica viene gestita interamente da noi: dalla delibera assembleare al collaudo, passando per la cessione del credito o il finanziamento. I condomini non anticipano nulla e iniziano a vedere la riduzione in bolletta dal primo mese di attivazione.</p>
          <p style="margin:0 0 18px 0;">Se le va, possiamo fare un breve confronto telefonico — quindici minuti per capire se c'è uno o due edifici nel suo portafoglio dove avrebbe senso fare una valutazione tecnica gratuita. Senza alcun impegno, ovviamente.</p>
          <p style="margin:0 0 18px 0;">Mi faccia sapere se può andare bene una mattina della prossima settimana.</p>
          <p style="margin:0 0 4px 0;">Un cordiale saluto,</p>
          <p style="margin:0;font-weight:600;">{{ sender_first_name }}<br><span style="font-weight:400;color:#64748b;font-size:15px;">{{ tenant_name }}</span></p>
        </td></tr>
        <tr><td style="border-top:1px solid #e5e7eb;padding-top:18px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:11px;line-height:1.6;color:#94a3b8;">
          {{ tenant_legal_name }} · P.IVA {{ tenant_vat_number }} · {{ tenant_legal_address }}<br>
          <a href="{{ unsubscribe_url }}" style="color:#94a3b8;text-decoration:underline;">Disiscriviti da queste comunicazioni</a>
        </td></tr>
      </table>
    </td></tr>
  </table>
  <img src="{{ tracking_pixel_url }}" alt="" width="1" height="1" style="display:none;width:1px;height:1px;border:0;">
</body>
</html>$html$;

  v_text_var_b := $txt$Buongiorno {{ greeting_name }},

Le scrivo perché ho notato che {{ business_name }} amministra immobili nella zona di {{ hq_city }} e volevo condividere un'esperienza recente.

Stiamo lavorando con altri studi di amministrazione condominiale qui intorno per installare impianti fotovoltaici sui tetti dei condomini gestiti. La parte che è piaciuta di più agli amministratori è che la pratica viene gestita interamente da noi: dalla delibera assembleare al collaudo, passando per la cessione del credito o il finanziamento. I condomini non anticipano nulla e iniziano a vedere la riduzione in bolletta dal primo mese di attivazione.

Se le va, possiamo fare un breve confronto telefonico — quindici minuti per capire se c'è uno o due edifici nel suo portafoglio dove avrebbe senso fare una valutazione tecnica gratuita. Senza alcun impegno, ovviamente.

Mi faccia sapere se può andare bene una mattina della prossima settimana.

Un cordiale saluto,
{{ sender_first_name }}
{{ tenant_name }}

---
{{ tenant_legal_name }} · P.IVA {{ tenant_vat_number }} · {{ tenant_legal_address }}
Disiscriviti: {{ unsubscribe_url }}$txt$;

  -- ── Insertions (idempotent on tenant_id + name) ──────────────────
  INSERT INTO email_templates (tenant_id, name, subject, html, plain_text, variables_used)
  SELECT p_tenant_id,
         'Base professionale',
         '{{ business_name }}: ridurre i costi energetici dei vostri condomini',
         v_html_base,
         v_text_base,
         v_vars_with_logo
  WHERE NOT EXISTS (
    SELECT 1 FROM email_templates
    WHERE tenant_id = p_tenant_id AND name = 'Base professionale'
  );

  INSERT INTO email_templates (tenant_id, name, subject, html, plain_text, variables_used)
  SELECT p_tenant_id,
         'A/B Variante A — Diretto',
         'Fino al 30% di risparmio in bolletta condominiale a {{ hq_city }}',
         v_html_var_a,
         v_text_var_a,
         v_vars_no_logo
  WHERE NOT EXISTS (
    SELECT 1 FROM email_templates
    WHERE tenant_id = p_tenant_id AND name = 'A/B Variante A — Diretto'
  );

  INSERT INTO email_templates (tenant_id, name, subject, html, plain_text, variables_used)
  SELECT p_tenant_id,
         'A/B Variante B — Conversazionale',
         'Un confronto rapido sui condomini di {{ business_name }}',
         v_html_var_b,
         v_text_var_b,
         v_vars_no_logo
  WHERE NOT EXISTS (
    SELECT 1 FROM email_templates
    WHERE tenant_id = p_tenant_id AND name = 'A/B Variante B — Conversazionale'
  );

END;
$func$;

-- Trigger: auto-seed for every newly-created tenant.
CREATE OR REPLACE FUNCTION trg_seed_email_templates_on_tenant_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $trg$
BEGIN
  PERFORM seed_default_email_templates_for_tenant(NEW.id);
  RETURN NEW;
END;
$trg$;

DROP TRIGGER IF EXISTS tenants_seed_email_templates ON tenants;
CREATE TRIGGER tenants_seed_email_templates
  AFTER INSERT ON tenants
  FOR EACH ROW EXECUTE FUNCTION trg_seed_email_templates_on_tenant_insert();

-- Backfill: seed for every existing tenant (idempotent).
SELECT seed_default_email_templates_for_tenant(id) FROM tenants;

COMMIT;
