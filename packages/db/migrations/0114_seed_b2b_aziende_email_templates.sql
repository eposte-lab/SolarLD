-- 0114 — seed 6 polished email templates per tenant.
--
-- Replaces the 0113 seed (3 condomini-only templates) with a richer
-- set tuned for the actual outreach mix:
--
--   B2B aziende (primary use case):
--     1. B2B Aziende — ROI & ammortamento operativo
--     2. B2B Aziende — Costo zero (cessione del credito)
--     3. B2B Aziende — Sostenibilità & ESG
--
--   Amministratori di condominio (secondary):
--     4. Condomini — Base professionale (rev.)
--     5. Condomini — Costo zero per i condomini
--     6. Condomini — Conversazionale (rev.)
--
-- All six start with a hero block that renders the Remotion-generated
-- GIF (`hero_gif_url`, fallback `hero_image_url`) at the very top of
-- the email — both vars are now wired through render_template_for_lead
-- in apps/api/src/routes/email_templates.py from the lead's rendering
-- pipeline (rendering_gif_cdn_url → rendering_gif_url → rendering_image_url).
--
-- Anti-spam compliance verified against services/content_validator.py:
--   - Subject ≤ 65 chars (after Jinja2 expansion in typical case)
--   - No SPAM_SUBJECT/BODY trigger words ("gratis", "gratuito",
--     "urgente", "solo oggi", "sconto del", "clicca qui", etc.)
--   - ≤ 6 links per template (1 CTA + 1 unsubscribe = 2)
--   - ≤ 8 images per template (1 hero + 1 tracking pixel + optional logo = 3)
--   - GDPR footer with the four required vars on every template.
--
-- Idempotency: each INSERT keys on (tenant_id, name) — re-running this
-- migration does not duplicate or overwrite operator edits.

BEGIN;

CREATE OR REPLACE FUNCTION seed_default_email_templates_v2_for_tenant(p_tenant_id UUID)
RETURNS void
LANGUAGE plpgsql
AS $func$
DECLARE
  -- Variable manifests stored as JSONB arrays (matches 0113 convention).
  v_vars JSONB := '["brand_logo_url","business_name","greeting_name","hero_gif_url","hero_image_url","hq_address","hq_cap","hq_city","hq_province","phone","sender_first_name","tenant_legal_address","tenant_legal_name","tenant_name","tenant_vat_number","tracking_pixel_url","unsubscribe_url"]'::jsonb;

  -- Per-template HTML + plain-text bodies.
  v_html_b2b_roi      TEXT;
  v_text_b2b_roi      TEXT;
  v_html_b2b_zero     TEXT;
  v_text_b2b_zero     TEXT;
  v_html_b2b_esg      TEXT;
  v_text_b2b_esg      TEXT;
  v_html_cond_base    TEXT;
  v_text_cond_base    TEXT;
  v_html_cond_zero    TEXT;
  v_text_cond_zero    TEXT;
  v_html_cond_chat    TEXT;
  v_text_cond_chat    TEXT;
BEGIN

  -- ════════════════════════════════════════════════════════════════════
  -- 1) B2B Aziende — ROI & ammortamento operativo
  -- ════════════════════════════════════════════════════════════════════
  v_html_b2b_roi := $html$<!DOCTYPE html>
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
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:14px;box-shadow:0 1px 3px rgba(0,0,0,0.05);overflow:hidden;">
        {% if hero_gif_url %}
        <tr><td style="padding:0;line-height:0;">
          <img src="{{ hero_gif_url }}" alt="{{ business_name }} — proiezione fotovoltaica" width="600" style="display:block;width:100%;max-width:600px;height:auto;border:0;">
        </td></tr>
        {% elif hero_image_url %}
        <tr><td style="padding:0;line-height:0;">
          <img src="{{ hero_image_url }}" alt="{{ business_name }} — proiezione fotovoltaica" width="600" style="display:block;width:100%;max-width:600px;height:auto;border:0;">
        </td></tr>
        {% endif %}
        <tr><td style="padding:28px 36px 8px 36px;">
          <p style="margin:0;font-size:11px;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:#0f766e;">Fotovoltaico industriale · {{ hq_city }}</p>
          <h1 style="margin:8px 0 0 0;font-size:22px;line-height:1.3;font-weight:700;color:#0f172a;letter-spacing:-0.01em;">Ridurre i costi energetici di {{ business_name }} con un impianto autoprodotto</h1>
        </td></tr>
        <tr><td style="padding:14px 36px 0 36px;font-size:15px;line-height:1.65;color:#334155;">
          <p style="margin:0 0 12px 0;">{{ greeting_name }}, sono {{ sender_first_name }} di {{ tenant_name }}. Ci occupiamo di impianti fotovoltaici per stabilimenti produttivi e commerciali, e abbiamo notato che la copertura del vostro immobile a {{ hq_city }} è ben orientata per l'autoproduzione di energia.</p>
          <p style="margin:0 0 4px 0;">In sintesi, i tre numeri che contano per un'azienda come la vostra:</p>
        </td></tr>
        <tr><td style="padding:12px 36px 0 36px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0f172a;border-radius:10px;color:#ffffff;">
            <tr>
              <td width="33%" style="padding:18px 12px;text-align:center;border-right:1px solid #1f2937;">
                <p style="margin:0;font-size:11px;font-weight:600;letter-spacing:0.12em;text-transform:uppercase;color:#94a3b8;">Potenza stimata</p>
                <p style="margin:6px 0 0 0;font-size:24px;font-weight:800;color:#ffffff;letter-spacing:-0.01em;">80–250 kWp</p>
              </td>
              <td width="34%" style="padding:18px 12px;text-align:center;border-right:1px solid #1f2937;">
                <p style="margin:0;font-size:11px;font-weight:600;letter-spacing:0.12em;text-transform:uppercase;color:#94a3b8;">Risparmio annuo</p>
                <p style="margin:6px 0 0 0;font-size:24px;font-weight:800;color:#14b8a6;letter-spacing:-0.01em;">25–40 %</p>
              </td>
              <td width="33%" style="padding:18px 12px;text-align:center;">
                <p style="margin:0;font-size:11px;font-weight:600;letter-spacing:0.12em;text-transform:uppercase;color:#94a3b8;">Rientro investimento</p>
                <p style="margin:6px 0 0 0;font-size:24px;font-weight:800;color:#ffffff;letter-spacing:-0.01em;">4–6 anni</p>
              </td>
            </tr>
          </table>
        </td></tr>
        <tr><td style="padding:22px 36px 0 36px;font-size:15px;line-height:1.65;color:#334155;">
          <p style="margin:0 0 8px 0;font-weight:600;color:#0f172a;">Cosa cambia in pratica per la vostra attività</p>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr><td style="padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:14px;color:#0f172a;"><span style="color:#0f766e;font-weight:700;">→</span>&nbsp;&nbsp;<strong>Autoconsumo immediato</strong> dell'energia prodotta nelle ore di attività, con riduzione diretta della componente energia in bolletta.</td></tr>
            <tr><td style="padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:14px;color:#0f172a;"><span style="color:#0f766e;font-weight:700;">→</span>&nbsp;&nbsp;<strong>Detrazione fiscale + super-ammortamento</strong> sull'investimento in beni strumentali (Industria 4.0, ove applicabile).</td></tr>
            <tr><td style="padding:8px 0;font-size:14px;color:#0f172a;"><span style="color:#0f766e;font-weight:700;">→</span>&nbsp;&nbsp;<strong>Indipendenza tariffaria</strong> dalle oscillazioni del PUN sui consumi auto-prodotti per i prossimi 25 anni di vita utile dell'impianto.</td></tr>
          </table>
        </td></tr>
        <tr><td style="padding:24px 36px 4px 36px;text-align:center;">
          <a href="mailto:?subject=Richiesta%20sopralluogo%20fotovoltaico%20-%20{{ business_name }}" style="display:inline-block;padding:13px 28px;background:#0f766e;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600;font-size:15px;">Richiedi un sopralluogo tecnico</a>
          <p style="margin:12px 0 0 0;font-size:12px;color:#64748b;">Sopralluogo tecnico senza impegno · stima personalizzata in 7–10 giorni</p>
        </td></tr>
        <tr><td style="padding:22px 36px 24px 36px;font-size:14px;line-height:1.6;color:#334155;border-top:1px solid #e5e7eb;margin-top:24px;">
          <p style="margin:18px 0 0 0;">Resto a disposizione,</p>
          <p style="margin:6px 0 0 0;font-weight:600;color:#0f172a;">{{ sender_first_name }}<br><span style="font-weight:400;color:#64748b;">{{ tenant_name }}</span></p>
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

  v_text_b2b_roi := $txt${{ greeting_name }},

Sono {{ sender_first_name }} di {{ tenant_name }}. Ci occupiamo di impianti fotovoltaici per stabilimenti produttivi e commerciali, e abbiamo notato che la copertura del vostro immobile a {{ hq_city }} è ben orientata per l'autoproduzione di energia.

I tre numeri che contano per un'azienda come la vostra:
- Potenza stimata: 80–250 kWp
- Risparmio annuo in bolletta: 25–40 %
- Rientro investimento: 4–6 anni

Cosa cambia in pratica:
→ Autoconsumo immediato dell'energia prodotta nelle ore di attività.
→ Detrazione fiscale e super-ammortamento sull'investimento (Industria 4.0, ove applicabile).
→ Indipendenza tariffaria dalle oscillazioni del PUN per i 25 anni di vita utile dell'impianto.

Possiamo organizzare un sopralluogo tecnico senza impegno: stima personalizzata pronta in 7–10 giorni.

Resto a disposizione,
{{ sender_first_name }}
{{ tenant_name }}

---
{{ tenant_legal_name }} · P.IVA {{ tenant_vat_number }}
{{ tenant_legal_address }}
Disiscriviti: {{ unsubscribe_url }}$txt$;

  -- ════════════════════════════════════════════════════════════════════
  -- 2) B2B Aziende — Costo zero (cessione del credito / finanziamento)
  -- ════════════════════════════════════════════════════════════════════
  v_html_b2b_zero := $html$<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>{{ business_name }}</title>
</head>
<body style="margin:0;padding:0;background:#0b3d2e;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2937;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0b3d2e;padding:36px 12px;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,0.18);">
        {% if hero_gif_url %}
        <tr><td style="padding:0;line-height:0;">
          <img src="{{ hero_gif_url }}" alt="{{ business_name }} — proiezione fotovoltaica" width="600" style="display:block;width:100%;max-width:600px;height:auto;border:0;">
        </td></tr>
        {% elif hero_image_url %}
        <tr><td style="padding:0;line-height:0;">
          <img src="{{ hero_image_url }}" alt="{{ business_name }} — proiezione fotovoltaica" width="600" style="display:block;width:100%;max-width:600px;height:auto;border:0;">
        </td></tr>
        {% endif %}
        <tr><td style="padding:0;background:linear-gradient(90deg,#15803d 0%,#22c55e 100%);">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="padding:14px 24px;text-align:center;color:#ffffff;font-size:13px;font-weight:600;letter-spacing:0.04em;">
                0 € di anticipo · cessione del credito · rate sostenute dal risparmio in bolletta
              </td>
            </tr>
          </table>
        </td></tr>
        <tr><td style="padding:28px 36px 8px 36px;">
          <h1 style="margin:0;font-size:24px;line-height:1.3;font-weight:700;color:#0f172a;letter-spacing:-0.01em;">Fotovoltaico per {{ business_name }} senza investimento iniziale</h1>
        </td></tr>
        <tr><td style="padding:14px 36px 0 36px;font-size:15px;line-height:1.65;color:#334155;">
          <p style="margin:0 0 12px 0;">{{ greeting_name }}, sono {{ sender_first_name }} di {{ tenant_name }}. Lavoriamo con istituti finanziari convenzionati per portare il fotovoltaico nelle aziende del territorio di {{ hq_city }} senza richiedere capitale iniziale.</p>
          <p style="margin:0 0 4px 0;">Il meccanismo, in tre passaggi:</p>
        </td></tr>
        <tr><td style="padding:14px 36px 0 36px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td valign="top" width="44" style="padding-top:2px;">
                <span style="display:inline-block;width:32px;height:32px;line-height:32px;text-align:center;border-radius:50%;background:#0b3d2e;color:#ffffff;font-weight:700;font-size:14px;">1</span>
              </td>
              <td valign="top" style="padding-bottom:14px;font-size:14px;line-height:1.6;color:#0f172a;">
                <strong>Sopralluogo tecnico e progetto preliminare.</strong>
                <span style="color:#475569;">Verifichiamo la copertura, l'orientamento e i consumi dello stabilimento per dimensionare l'impianto.</span>
              </td>
            </tr>
            <tr>
              <td valign="top" width="44" style="padding-top:2px;">
                <span style="display:inline-block;width:32px;height:32px;line-height:32px;text-align:center;border-radius:50%;background:#0b3d2e;color:#ffffff;font-weight:700;font-size:14px;">2</span>
              </td>
              <td valign="top" style="padding-bottom:14px;font-size:14px;line-height:1.6;color:#0f172a;">
                <strong>Pratica di finanziamento o cessione del credito.</strong>
                <span style="color:#475569;">Curiamo la documentazione con i partner bancari; la rata mensile è calibrata per restare sotto il risparmio energetico atteso.</span>
              </td>
            </tr>
            <tr>
              <td valign="top" width="44" style="padding-top:2px;">
                <span style="display:inline-block;width:32px;height:32px;line-height:32px;text-align:center;border-radius:50%;background:#0b3d2e;color:#ffffff;font-weight:700;font-size:14px;">3</span>
              </td>
              <td valign="top" style="padding-bottom:14px;font-size:14px;line-height:1.6;color:#0f172a;">
                <strong>Installazione, allacciamento e monitoraggio.</strong>
                <span style="color:#475569;">Tempi medi 8–12 settimane dal contratto. Portale di monitoraggio incluso, manutenzione pluriennale opzionale.</span>
              </td>
            </tr>
          </table>
        </td></tr>
        <tr><td style="padding:18px 36px 0 36px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f0fdf4;border:1px solid #86efac;border-radius:10px;">
            <tr><td style="padding:16px 18px;font-size:13px;line-height:1.55;color:#166534;">
              <strong>Cosa significa per il vostro bilancio:</strong> nessun esborso iniziale, costi sostituiti dalla rata di finanziamento, risparmio energetico al netto della rata già dal primo anno.
            </td></tr>
          </table>
        </td></tr>
        <tr><td style="padding:24px 36px 6px 36px;text-align:center;">
          <a href="mailto:?subject=Verifica%20fattibilita%20fotovoltaico%20a%20costo%20zero%20-%20{{ business_name }}" style="display:inline-block;padding:13px 28px;background:#0b3d2e;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600;font-size:15px;">Verifica la fattibilità senza impegno</a>
          <p style="margin:12px 0 0 0;font-size:12px;color:#64748b;">Risposta in 5 giorni lavorativi · nessun documento richiesto in questa fase</p>
        </td></tr>
        <tr><td style="padding:22px 36px 24px 36px;font-size:14px;line-height:1.6;color:#334155;border-top:1px solid #e5e7eb;margin-top:18px;">
          <p style="margin:18px 0 0 0;">Buona giornata,</p>
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

  v_text_b2b_zero := $txt${{ greeting_name }},

Sono {{ sender_first_name }} di {{ tenant_name }}. Lavoriamo con istituti finanziari convenzionati per portare il fotovoltaico nelle aziende di {{ hq_city }} senza investimento iniziale.

— 0 € di anticipo · cessione del credito · rate sostenute dal risparmio in bolletta —

Il meccanismo, in tre passaggi:
1. Sopralluogo tecnico e progetto preliminare. Verifichiamo copertura, orientamento e consumi per dimensionare l'impianto.
2. Pratica di finanziamento o cessione del credito. Curiamo la documentazione con i partner bancari; la rata è calibrata per restare sotto il risparmio energetico atteso.
3. Installazione, allacciamento e monitoraggio. Tempi medi 8–12 settimane dal contratto.

Cosa significa per il bilancio: nessun esborso iniziale, costi sostituiti dalla rata di finanziamento, risparmio energetico al netto della rata già dal primo anno.

Possiamo verificare la fattibilità senza impegno — risposta in 5 giorni lavorativi, nessun documento richiesto in questa fase.

Buona giornata,
{{ sender_first_name }} · {{ tenant_name }}

---
{{ tenant_legal_name }} · P.IVA {{ tenant_vat_number }}
{{ tenant_legal_address }}
Disiscriviti: {{ unsubscribe_url }}$txt$;

  -- ════════════════════════════════════════════════════════════════════
  -- 3) B2B Aziende — Sostenibilità & ESG
  -- ════════════════════════════════════════════════════════════════════
  v_html_b2b_esg := $html$<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>{{ business_name }}</title>
</head>
<body style="margin:0;padding:0;background:#fafaf9;font-family:Georgia,'Times New Roman',Times,serif;color:#1c1917;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#fafaf9;padding:40px 12px;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border:1px solid #e7e5e4;border-radius:6px;overflow:hidden;">
        {% if hero_gif_url %}
        <tr><td style="padding:0;line-height:0;">
          <img src="{{ hero_gif_url }}" alt="{{ business_name }} — proiezione fotovoltaica" width="600" style="display:block;width:100%;max-width:600px;height:auto;border:0;">
        </td></tr>
        {% elif hero_image_url %}
        <tr><td style="padding:0;line-height:0;">
          <img src="{{ hero_image_url }}" alt="{{ business_name }} — proiezione fotovoltaica" width="600" style="display:block;width:100%;max-width:600px;height:auto;border:0;">
        </td></tr>
        {% endif %}
        <tr><td style="padding:32px 40px 6px 40px;">
          <p style="margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:11px;font-weight:700;letter-spacing:0.20em;text-transform:uppercase;color:#78716c;">Sostenibilità d'impresa</p>
          <h1 style="margin:8px 0 0 0;font-size:26px;line-height:1.3;font-weight:400;color:#1c1917;letter-spacing:-0.01em;">Il percorso di {{ business_name }} verso il bilancio CO<sub>2</sub> in pareggio</h1>
        </td></tr>
        <tr><td style="padding:18px 40px 0 40px;font-size:16px;line-height:1.7;color:#44403c;">
          <p style="margin:0 0 16px 0;">{{ greeting_name }}, sono {{ sender_first_name }} di {{ tenant_name }}. Le scrivo perché un numero crescente di aziende del territorio sta integrando il fotovoltaico non solo come leva di risparmio, ma come voce concreta del proprio bilancio di sostenibilità.</p>
          <p style="margin:0 0 16px 0;">Per un'azienda con un consumo medio di un capannone produttivo, un impianto da 100&nbsp;kWp evita l'emissione di circa <strong>40 tonnellate di CO<sub>2</sub> all'anno</strong> — l'equivalente di un piccolo bosco urbano.</p>
        </td></tr>
        <tr><td style="padding:14px 40px 0 40px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e7e5e4;border-bottom:1px solid #e7e5e4;">
            <tr>
              <td width="50%" valign="top" style="padding:18px 14px 18px 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.55;color:#44403c;border-right:1px solid #e7e5e4;">
                <p style="margin:0 0 6px 0;font-weight:700;color:#1c1917;font-size:12px;letter-spacing:0.10em;text-transform:uppercase;">Cosa cambia in bilancio</p>
                <p style="margin:0;">Riduzione tCO<sub>2</sub> annue, certificato di garanzia di origine (GO) sull'energia auto-prodotta, rendicontazione automatica per il bilancio di sostenibilità.</p>
              </td>
              <td width="50%" valign="top" style="padding:18px 0 18px 14px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.55;color:#44403c;">
                <p style="margin:0 0 6px 0;font-weight:700;color:#1c1917;font-size:12px;letter-spacing:0.10em;text-transform:uppercase;">Cosa cambia per la filiera</p>
                <p style="margin:0;">Risposte concrete alle richieste ESG dei clienti enterprise, idoneità per gare pubbliche con criteri ambientali minimi (CAM), percorso facilitato verso certificazioni come B-Corp.</p>
              </td>
            </tr>
          </table>
        </td></tr>
        <tr><td style="padding:22px 40px 0 40px;font-size:16px;line-height:1.7;color:#44403c;">
          <p style="margin:0;">Posso preparare per voi un report di fattibilità che include la stima dell'impatto in bilancio CO<sub>2</sub> e la simulazione tecnico-economica sul vostro stabilimento. Senza impegno, in formato PDF, condivisibile direttamente con il vostro CdA.</p>
        </td></tr>
        <tr><td style="padding:24px 40px 6px 40px;">
          <a href="mailto:?subject=Report%20fattibilita%20ESG%20fotovoltaico%20-%20{{ business_name }}" style="display:inline-block;padding:12px 24px;background:#1c1917;color:#ffffff;text-decoration:none;border-radius:4px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-weight:600;font-size:14px;letter-spacing:0.02em;">Richiedi il report di fattibilità</a>
        </td></tr>
        <tr><td style="padding:22px 40px 26px 40px;font-size:15px;line-height:1.7;color:#44403c;">
          <p style="margin:14px 0 0 0;">Un cordiale saluto,</p>
          <p style="margin:6px 0 0 0;">{{ sender_first_name }}<br><span style="color:#78716c;">{{ tenant_name }}</span></p>
        </td></tr>
        <tr><td style="border-top:1px solid #e7e5e4;padding:18px 40px;background:#fafaf9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:11px;line-height:1.6;color:#a8a29e;">
          {{ tenant_legal_name }} · P.IVA {{ tenant_vat_number }} · {{ tenant_legal_address }}<br>
          <a href="{{ unsubscribe_url }}" style="color:#a8a29e;text-decoration:underline;">Disiscriviti da queste comunicazioni</a>
        </td></tr>
      </table>
    </td></tr>
  </table>
  <img src="{{ tracking_pixel_url }}" alt="" width="1" height="1" style="display:none;width:1px;height:1px;border:0;">
</body>
</html>$html$;

  v_text_b2b_esg := $txt${{ greeting_name }},

Sono {{ sender_first_name }} di {{ tenant_name }}. Le scrivo perché un numero crescente di aziende del territorio sta integrando il fotovoltaico non solo come leva di risparmio, ma come voce concreta del proprio bilancio di sostenibilità.

Per un'azienda con un consumo medio di un capannone produttivo, un impianto da 100 kWp evita l'emissione di circa 40 tonnellate di CO2 all'anno — l'equivalente di un piccolo bosco urbano.

Cosa cambia in bilancio:
Riduzione tCO2 annue, certificato di garanzia di origine sull'energia auto-prodotta, rendicontazione automatica per il bilancio di sostenibilità.

Cosa cambia per la filiera:
Risposte concrete alle richieste ESG dei clienti enterprise, idoneità per gare pubbliche con criteri ambientali minimi (CAM), percorso facilitato verso certificazioni come B-Corp.

Posso preparare per voi un report di fattibilità che include la stima dell'impatto in bilancio CO2 e la simulazione tecnico-economica sul vostro stabilimento. Senza impegno, in formato PDF, condivisibile con il vostro CdA.

Un cordiale saluto,
{{ sender_first_name }}
{{ tenant_name }}

---
{{ tenant_legal_name }} · P.IVA {{ tenant_vat_number }} · {{ tenant_legal_address }}
Disiscriviti: {{ unsubscribe_url }}$txt$;

  -- ════════════════════════════════════════════════════════════════════
  -- 4) Condomini — Base professionale (rev.)
  -- ════════════════════════════════════════════════════════════════════
  v_html_cond_base := $html$<!DOCTYPE html>
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
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:14px;box-shadow:0 1px 3px rgba(0,0,0,0.05);overflow:hidden;">
        {% if hero_gif_url %}
        <tr><td style="padding:0;line-height:0;">
          <img src="{{ hero_gif_url }}" alt="{{ business_name }} — proiezione fotovoltaica" width="600" style="display:block;width:100%;max-width:600px;height:auto;border:0;">
        </td></tr>
        {% elif hero_image_url %}
        <tr><td style="padding:0;line-height:0;">
          <img src="{{ hero_image_url }}" alt="{{ business_name }} — proiezione fotovoltaica" width="600" style="display:block;width:100%;max-width:600px;height:auto;border:0;">
        </td></tr>
        {% endif %}
        <tr><td style="padding:28px 36px 6px 36px;">
          <div style="height:3px;width:48px;background:#0f766e;border-radius:2px;margin-bottom:16px;"></div>
          <p style="margin:0;font-size:11px;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:#0f766e;">Fotovoltaico condominiale · {{ hq_city }}</p>
          <h1 style="margin:8px 0 0 0;font-size:22px;line-height:1.3;font-weight:700;color:#0f172a;letter-spacing:-0.01em;">Buongiorno {{ greeting_name }}</h1>
        </td></tr>
        <tr><td style="padding:14px 36px 0 36px;font-size:15px;line-height:1.65;color:#334155;">
          <p style="margin:0 0 12px 0;">Sono {{ sender_first_name }} di {{ tenant_name }}. Mi occupo di soluzioni di efficientamento energetico per gli edifici gestiti da studi di amministrazione come {{ business_name }}.</p>
          <p style="margin:0 0 12px 0;">Aiutiamo gli amministratori a portare ai propri condomini un impianto fotovoltaico chiavi in mano: pratica, finanziamento e installazione gestiti interamente da noi, riducendo le spese condominiali ricorrenti.</p>
          <p style="margin:0 0 4px 0;font-weight:600;color:#0f172a;">Cosa rendiamo semplice per gli amministratori:</p>
        </td></tr>
        <tr><td style="padding:8px 36px 0 36px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr><td style="padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:14px;color:#0f172a;"><span style="color:#0f766e;font-weight:700;">→</span>&nbsp;&nbsp;<strong>Delibera assembleare assistita.</strong> Forniamo presentazione, simulazioni e Q&amp;A pre-pronte per l'assemblea.</td></tr>
            <tr><td style="padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:14px;color:#0f172a;"><span style="color:#0f766e;font-weight:700;">→</span>&nbsp;&nbsp;<strong>Pratica completa chiavi in mano.</strong> Progetto, GSE, allacciamento e collaudo gestiti dal nostro team.</td></tr>
            <tr><td style="padding:8px 0;font-size:14px;color:#0f172a;"><span style="color:#0f766e;font-weight:700;">→</span>&nbsp;&nbsp;<strong>Stima del risparmio per ogni edificio.</strong> Bastano l'indirizzo e qualche dato di consumo, vi mandiamo la simulazione in 7 giorni.</td></tr>
          </table>
        </td></tr>
        <tr><td style="padding:24px 36px 4px 36px;text-align:center;">
          <a href="mailto:?subject=Stima%20fotovoltaico%20condominiale%20-%20{{ business_name }}" style="display:inline-block;padding:13px 28px;background:#0f766e;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600;font-size:15px;">Richiedi una stima per uno dei vostri immobili</a>
          <p style="margin:12px 0 0 0;font-size:12px;color:#64748b;">Stima personalizzata · nessun impegno per il condominio</p>
        </td></tr>
        <tr><td style="padding:22px 36px 24px 36px;font-size:14px;line-height:1.6;color:#334155;border-top:1px solid #e5e7eb;margin-top:18px;">
          <p style="margin:18px 0 0 0;">Resto a disposizione,</p>
          <p style="margin:6px 0 0 0;font-weight:600;color:#0f172a;">{{ sender_first_name }}<br><span style="font-weight:400;color:#64748b;">{{ tenant_name }}</span></p>
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

  v_text_cond_base := $txt$Buongiorno {{ greeting_name }},

Sono {{ sender_first_name }} di {{ tenant_name }}. Mi occupo di soluzioni di efficientamento energetico per gli edifici gestiti da studi di amministrazione come {{ business_name }}.

Aiutiamo gli amministratori a portare ai propri condomini un impianto fotovoltaico chiavi in mano: pratica, finanziamento e installazione gestiti interamente da noi, riducendo le spese condominiali ricorrenti.

Cosa rendiamo semplice per gli amministratori:
→ Delibera assembleare assistita: presentazione, simulazioni e Q&A pre-pronte per l'assemblea.
→ Pratica completa chiavi in mano: progetto, GSE, allacciamento e collaudo gestiti dal nostro team.
→ Stima del risparmio per ogni edificio: bastano l'indirizzo e qualche dato di consumo, vi mandiamo la simulazione in 7 giorni.

Resto a disposizione,
{{ sender_first_name }}
{{ tenant_name }}

---
{{ tenant_legal_name }} · P.IVA {{ tenant_vat_number }}
{{ tenant_legal_address }}
Disiscriviti: {{ unsubscribe_url }}$txt$;

  -- ════════════════════════════════════════════════════════════════════
  -- 5) Condomini — Costo zero per i condomini
  -- ════════════════════════════════════════════════════════════════════
  v_html_cond_zero := $html$<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>{{ business_name }}</title>
</head>
<body style="margin:0;padding:0;background:#0b3d2e;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2937;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0b3d2e;padding:36px 12px;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 8px 30px rgba(0,0,0,0.18);">
        {% if hero_gif_url %}
        <tr><td style="padding:0;line-height:0;">
          <img src="{{ hero_gif_url }}" alt="{{ business_name }} — proiezione fotovoltaica" width="600" style="display:block;width:100%;max-width:600px;height:auto;border:0;">
        </td></tr>
        {% elif hero_image_url %}
        <tr><td style="padding:0;line-height:0;">
          <img src="{{ hero_image_url }}" alt="{{ business_name }} — proiezione fotovoltaica" width="600" style="display:block;width:100%;max-width:600px;height:auto;border:0;">
        </td></tr>
        {% endif %}
        <tr><td style="padding:0;background:linear-gradient(90deg,#15803d 0%,#22c55e 100%);">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="padding:14px 24px;text-align:center;color:#ffffff;font-size:13px;font-weight:600;letter-spacing:0.04em;">
                I condomini non anticipano nulla · rate coperte dal risparmio in bolletta
              </td>
            </tr>
          </table>
        </td></tr>
        <tr><td style="padding:28px 36px 8px 36px;">
          <h1 style="margin:0;font-size:24px;line-height:1.3;font-weight:700;color:#0f172a;letter-spacing:-0.01em;">Fotovoltaico per i condomini di {{ business_name }} senza esborso iniziale</h1>
        </td></tr>
        <tr><td style="padding:14px 36px 0 36px;font-size:15px;line-height:1.65;color:#334155;">
          <p style="margin:0 0 12px 0;">Buongiorno {{ greeting_name }}, sono {{ sender_first_name }} di {{ tenant_name }}. Lavoriamo con istituti finanziari convenzionati per portare il fotovoltaico nei condomini gestiti da studi come il vostro a {{ hq_city }}.</p>
          <p style="margin:0 0 4px 0;">Lo schema che proponiamo agli amministratori e ai condomini in assemblea:</p>
        </td></tr>
        <tr><td style="padding:14px 36px 0 36px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td valign="top" width="44" style="padding-top:2px;">
                <span style="display:inline-block;width:32px;height:32px;line-height:32px;text-align:center;border-radius:50%;background:#0b3d2e;color:#ffffff;font-weight:700;font-size:14px;">1</span>
              </td>
              <td valign="top" style="padding-bottom:14px;font-size:14px;line-height:1.6;color:#0f172a;">
                <strong>Cessione del credito o finanziamento dedicato.</strong>
                <span style="color:#475569;">L'impianto viene installato e finanziato dai nostri partner. Nessun condomino versa anticipi.</span>
              </td>
            </tr>
            <tr>
              <td valign="top" width="44" style="padding-top:2px;">
                <span style="display:inline-block;width:32px;height:32px;line-height:32px;text-align:center;border-radius:50%;background:#0b3d2e;color:#ffffff;font-weight:700;font-size:14px;">2</span>
              </td>
              <td valign="top" style="padding-bottom:14px;font-size:14px;line-height:1.6;color:#0f172a;">
                <strong>Le rate del finanziamento vengono ripartite in bolletta condominiale.</strong>
                <span style="color:#475569;">L'importo è dimensionato per restare sotto il risparmio energetico atteso: il bilancio condominiale resta in equilibrio dal primo mese.</span>
              </td>
            </tr>
            <tr>
              <td valign="top" width="44" style="padding-top:2px;">
                <span style="display:inline-block;width:32px;height:32px;line-height:32px;text-align:center;border-radius:50%;background:#0b3d2e;color:#ffffff;font-weight:700;font-size:14px;">3</span>
              </td>
              <td valign="top" style="padding-bottom:14px;font-size:14px;line-height:1.6;color:#0f172a;">
                <strong>Estinto il finanziamento, l'energia prodotta è pura riduzione delle spese.</strong>
                <span style="color:#475569;">L'impianto resta di proprietà del condominio per i 25 anni di vita utile residua.</span>
              </td>
            </tr>
          </table>
        </td></tr>
        <tr><td style="padding:18px 36px 0 36px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f0fdf4;border:1px solid #86efac;border-radius:10px;">
            <tr><td style="padding:16px 18px;font-size:13px;line-height:1.55;color:#166534;">
              <strong>Materiali per l'assemblea:</strong> simulazione personalizzata sull'edificio, presentazione PowerPoint pronta, fac-simile di delibera. Tutto preparato dal nostro team prima della convocazione.
            </td></tr>
          </table>
        </td></tr>
        <tr><td style="padding:24px 36px 6px 36px;text-align:center;">
          <a href="mailto:?subject=Fotovoltaico%20a%20costo%20zero%20-%20{{ business_name }}" style="display:inline-block;padding:13px 28px;background:#0b3d2e;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600;font-size:15px;">Prepariamo i materiali per la prossima assemblea</a>
          <p style="margin:12px 0 0 0;font-size:12px;color:#64748b;">Indicate uno dei vostri condomini, vi inviamo la simulazione in 7 giorni</p>
        </td></tr>
        <tr><td style="padding:22px 36px 24px 36px;font-size:14px;line-height:1.6;color:#334155;border-top:1px solid #e5e7eb;margin-top:18px;">
          <p style="margin:18px 0 0 0;">Buona giornata,</p>
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

  v_text_cond_zero := $txt$Buongiorno {{ greeting_name }},

Sono {{ sender_first_name }} di {{ tenant_name }}. Lavoriamo con istituti finanziari convenzionati per portare il fotovoltaico nei condomini gestiti da studi come il vostro a {{ hq_city }}.

— I condomini non anticipano nulla · rate coperte dal risparmio in bolletta —

Lo schema che proponiamo agli amministratori e ai condomini in assemblea:
1. Cessione del credito o finanziamento dedicato. L'impianto viene installato e finanziato dai nostri partner. Nessun condomino versa anticipi.
2. Le rate del finanziamento vengono ripartite in bolletta condominiale. L'importo è dimensionato per restare sotto il risparmio energetico atteso: il bilancio condominiale resta in equilibrio dal primo mese.
3. Estinto il finanziamento, l'energia prodotta è pura riduzione delle spese. L'impianto resta di proprietà del condominio per i 25 anni di vita utile residua.

Materiali per l'assemblea: simulazione personalizzata sull'edificio, presentazione PowerPoint pronta, fac-simile di delibera. Tutto preparato dal nostro team prima della convocazione.

Possiamo preparare i materiali per la vostra prossima assemblea — indicate uno dei vostri condomini, vi inviamo la simulazione in 7 giorni.

Buona giornata,
{{ sender_first_name }} · {{ tenant_name }}

---
{{ tenant_legal_name }} · P.IVA {{ tenant_vat_number }}
{{ tenant_legal_address }}
Disiscriviti: {{ unsubscribe_url }}$txt$;

  -- ════════════════════════════════════════════════════════════════════
  -- 6) Condomini — Conversazionale (rev.)
  -- ════════════════════════════════════════════════════════════════════
  v_html_cond_chat := $html$<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>{{ business_name }}</title>
</head>
<body style="margin:0;padding:0;background:#ffffff;font-family:Georgia,'Times New Roman',Times,serif;color:#1f2937;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">
        {% if hero_gif_url %}
        <tr><td style="padding:0 0 28px 0;line-height:0;">
          <img src="{{ hero_gif_url }}" alt="{{ business_name }} — proiezione fotovoltaica" width="560" style="display:block;width:100%;max-width:560px;height:auto;border:0;border-radius:8px;">
        </td></tr>
        {% elif hero_image_url %}
        <tr><td style="padding:0 0 28px 0;line-height:0;">
          <img src="{{ hero_image_url }}" alt="{{ business_name }} — proiezione fotovoltaica" width="560" style="display:block;width:100%;max-width:560px;height:auto;border:0;border-radius:8px;">
        </td></tr>
        {% endif %}
        <tr><td style="padding:0 0 24px 0;font-size:17px;line-height:1.7;color:#1f2937;">
          <p style="margin:0 0 16px 0;">Buongiorno {{ greeting_name }},</p>
          <p style="margin:0 0 16px 0;">Le scrivo perché ho notato che {{ business_name }} amministra immobili nella zona di {{ hq_city }}, e volevo condividere un'esperienza recente.</p>
          <p style="margin:0 0 16px 0;">Stiamo lavorando con altri studi di amministrazione qui intorno per installare impianti fotovoltaici sui tetti dei condomini gestiti. La parte che è piaciuta di più agli amministratori è che la pratica viene gestita interamente da noi: dalla delibera assembleare al collaudo, passando per la cessione del credito o il finanziamento. I condomini non anticipano nulla e iniziano a vedere la riduzione in bolletta dal primo mese di attivazione.</p>
          <p style="margin:0 0 16px 0;">Se le va, possiamo fare un breve confronto telefonico — quindici minuti per capire se c'è uno o due edifici nel suo portafoglio dove avrebbe senso fare una valutazione tecnica. Senza alcun impegno per il condominio, ovviamente.</p>
          <p style="margin:0 0 4px 0;">Un cordiale saluto,</p>
          <p style="margin:0;font-weight:600;">{{ sender_first_name }}<br><span style="font-weight:400;color:#64748b;font-size:15px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">{{ tenant_name }}</span></p>
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

  v_text_cond_chat := $txt$Buongiorno {{ greeting_name }},

Le scrivo perché ho notato che {{ business_name }} amministra immobili nella zona di {{ hq_city }}, e volevo condividere un'esperienza recente.

Stiamo lavorando con altri studi di amministrazione qui intorno per installare impianti fotovoltaici sui tetti dei condomini gestiti. La parte che è piaciuta di più agli amministratori è che la pratica viene gestita interamente da noi: dalla delibera assembleare al collaudo, passando per la cessione del credito o il finanziamento. I condomini non anticipano nulla e iniziano a vedere la riduzione in bolletta dal primo mese di attivazione.

Se le va, possiamo fare un breve confronto telefonico — quindici minuti per capire se c'è uno o due edifici nel suo portafoglio dove avrebbe senso fare una valutazione tecnica. Senza alcun impegno per il condominio, ovviamente.

Un cordiale saluto,
{{ sender_first_name }}
{{ tenant_name }}

---
{{ tenant_legal_name }} · P.IVA {{ tenant_vat_number }} · {{ tenant_legal_address }}
Disiscriviti: {{ unsubscribe_url }}$txt$;

  -- ── Insertions (idempotent on tenant_id + name) ──────────────────

  INSERT INTO email_templates (tenant_id, name, subject, html, plain_text, variables_used)
  SELECT p_tenant_id,
         'B2B Aziende — ROI & ammortamento operativo',
         '{{ business_name }}: ridurre i costi energetici a {{ hq_city }}',
         v_html_b2b_roi, v_text_b2b_roi, v_vars
  WHERE NOT EXISTS (
    SELECT 1 FROM email_templates
    WHERE tenant_id = p_tenant_id AND name = 'B2B Aziende — ROI & ammortamento operativo'
  );

  INSERT INTO email_templates (tenant_id, name, subject, html, plain_text, variables_used)
  SELECT p_tenant_id,
         'B2B Aziende — Costo zero (cessione del credito)',
         'Fotovoltaico per {{ business_name }} senza investimento iniziale',
         v_html_b2b_zero, v_text_b2b_zero, v_vars
  WHERE NOT EXISTS (
    SELECT 1 FROM email_templates
    WHERE tenant_id = p_tenant_id AND name = 'B2B Aziende — Costo zero (cessione del credito)'
  );

  INSERT INTO email_templates (tenant_id, name, subject, html, plain_text, variables_used)
  SELECT p_tenant_id,
         'B2B Aziende — Sostenibilità & ESG',
         '{{ business_name }}: il percorso verso il bilancio CO2 in pareggio',
         v_html_b2b_esg, v_text_b2b_esg, v_vars
  WHERE NOT EXISTS (
    SELECT 1 FROM email_templates
    WHERE tenant_id = p_tenant_id AND name = 'B2B Aziende — Sostenibilità & ESG'
  );

  INSERT INTO email_templates (tenant_id, name, subject, html, plain_text, variables_used)
  SELECT p_tenant_id,
         'Condomini — Base professionale (rev.)',
         'Fotovoltaico condominiale per {{ business_name }}',
         v_html_cond_base, v_text_cond_base, v_vars
  WHERE NOT EXISTS (
    SELECT 1 FROM email_templates
    WHERE tenant_id = p_tenant_id AND name = 'Condomini — Base professionale (rev.)'
  );

  INSERT INTO email_templates (tenant_id, name, subject, html, plain_text, variables_used)
  SELECT p_tenant_id,
         'Condomini — Costo zero per i condomini',
         'Fotovoltaico per i condomini di {{ business_name }} senza esborso',
         v_html_cond_zero, v_text_cond_zero, v_vars
  WHERE NOT EXISTS (
    SELECT 1 FROM email_templates
    WHERE tenant_id = p_tenant_id AND name = 'Condomini — Costo zero per i condomini'
  );

  INSERT INTO email_templates (tenant_id, name, subject, html, plain_text, variables_used)
  SELECT p_tenant_id,
         'Condomini — Conversazionale (rev.)',
         'Un confronto rapido sui condomini di {{ business_name }}',
         v_html_cond_chat, v_text_cond_chat, v_vars
  WHERE NOT EXISTS (
    SELECT 1 FROM email_templates
    WHERE tenant_id = p_tenant_id AND name = 'Condomini — Conversazionale (rev.)'
  );

END;
$func$;

-- Trigger: auto-seed v2 templates for every newly-created tenant.
-- The 0113 trigger is left in place so existing tenants keep their
-- legacy templates; this trigger fires alongside it for new tenants.
CREATE OR REPLACE FUNCTION trg_seed_email_templates_v2_on_tenant_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $trg$
BEGIN
  PERFORM seed_default_email_templates_v2_for_tenant(NEW.id);
  RETURN NEW;
END;
$trg$;

DROP TRIGGER IF EXISTS tenants_seed_email_templates_v2 ON tenants;
CREATE TRIGGER tenants_seed_email_templates_v2
  AFTER INSERT ON tenants
  FOR EACH ROW EXECUTE FUNCTION trg_seed_email_templates_v2_on_tenant_insert();

-- Backfill: seed v2 templates for every existing tenant (idempotent).
SELECT seed_default_email_templates_v2_for_tenant(id) FROM tenants;

COMMIT;
