# QA Runbook — SolarLD Production Validation

**24 flows · 15-min smoke · 60-min full · Sprint A–C infrastructure**

This runbook validates every user-facing surface of SolarLD before going
to market. It assumes the demo seeder (Sprint B) has run and the staging
environment is healthy.

---

## Quick reference

| Mode | Flows | Time | When to use |
|------|-------|------|-------------|
| **Smoke** | 1–8 | ~15 min | After each deploy — verify nothing catastrophic broke |
| **Full** | 1–24 | ~60 min | Before a sales call, before a release to market |

---

## Preconditions

Complete these before starting the timer. Missing any of them will cause
false failures.

### Environment

- [ ] Railway API healthy: `curl https://<api-url>/health` → `{"status":"ok"}`
- [ ] Railway worker healthy: at least one arq process visible in Railway logs
- [ ] Dashboard deployed on Vercel, portal on Vercel (or local dev stack)
- [ ] Supabase project `ppabjpryzzkksrbnledy` (EU-West-1) accessible

### Demo data

```bash
# From apps/api/ — seeds Demo Solar Srl + 10 leads + preventivo + pratica
pnpm seed:demo:reset
```

Note the tenant UUID printed at the end — you'll need it for Admin flows.

### Test email inbox

Use a real inbox you control with **remote-image loading enabled** (Gmail
or Outlook — do NOT use Mailtrap for visual QA because it renders images
differently). Recommended: a Gmail alias `you+solarld@gmail.com`.

Set `DEMO_EMAIL_RECIPIENT_OVERRIDE=<your-qa-inbox>` in Railway so demo
emails land there instead of the prospect's typed address.

### Admin credentials

- Dashboard login: your super_admin Supabase user
- API super_admin JWT: generate with `supabase auth admin generateToken` or
  use the Railway `/v1/admin/system/health` sanity call to confirm your JWT
  has `role=super_admin`

---

## Smoke test — 15 minutes · Flows 1–8

> Run this after every deploy. If any step fails, stop and fix before proceeding.

### Flow 1 — Dashboard loads (1 min)

**Steps**
1. Open the dashboard URL in an incognito window.
2. Log in with your demo tenant credentials.

**Pass** Dashboard `/leads` renders with the "Pratiche post-firma" header
and at least 10 lead rows visible.

**Fail** Blank page / 500 → check Vercel function logs and Supabase RLS
policies. Common cause: expired JWT or missing env var.

---

### Flow 2 — Leads list and filters (2 min)

**Steps**
1. Navigate to `/leads`.
2. Confirm 10 rows are visible in the default view.
3. Click filter chip **"Documenti inviati"** → visible count drops.
4. Click **"Tutte"** → count returns to 10.
5. Click **"Scadenze aperte"** toggle → only leads with deadlines shown.

**Pass** Row count changes correctly for each filter; no JS errors in
console.

**Fail** Count doesn't change → `pipeline_status` enum mismatch or the
filter is querying the wrong column. Check `/v1/practices?status=...`
network request.

---

### Flow 3 — Lead detail (2 min)

**Steps**
1. Click the **"closed_won"** lead (Tessile Campana Srl).
2. Verify: score badge (91, hot tier), ROI card (kWp / risparmio / payback),
   anagrafica section (nome, email, telefono con chip fonte), feedback chip
   "Contratto firmato".
3. Scroll to the timeline — at least 2 events visible.

**Pass** All sections render with non-null data; no grey "—" placeholders
on the main fields.

**Fail** Empty ROI → `roi_data` JSONB missing keys. Empty timeline →
`events` query wrong tenant_id scope.

---

### Flow 4 — Pratiche list (1 min)

**Steps**
1. Navigate to `/practices`.
2. Confirm at least 1 row with practice number `DEMO/2026/0001`.
3. Click filter **"Documenti inviati"** → row remains (practice status is
   `documents_sent`).

**Pass** Practice row visible with correct number, status chip, kWp, and
"Apri →" link.

---

### Flow 5 — Pratica detail + documents (3 min)

**Steps**
1. From `/practices`, open `DEMO/2026/0001`.
2. Verify the header shows: numero pratica, cliente (Tessile Campana),
   stato (Documenti inviati), kWp 224.
3. In the **Documenti** section, confirm 9 rows with mixed statuses:
   - `dm_37_08` → Revisionato
   - `comunicazione_comune` → Inviato
   - `tica_areti` → Inviato
   - `modello_unico_p2` → Bozza
4. Click **"Apri pratica"** / breadcrumb links — navigation works.

**Pass** 9 document rows visible, statuses correct.

**Fail** Missing rows → `practice_documents` upsert constraint
`practice_id,template_code` may have silently no-oped.

---

### Flow 6 — Scadenze page (2 min)

**Steps**
1. Navigate to `/scadenze`.
2. Confirm 3 deadline rows visible.
3. Verify urgency chips:
   - **Comune silenzio-assenso** → red "X gg in ritardo" badge (overdue).
   - **TICA distributore** → amber "4 gg rimasti" badge (imminent).
   - **Transizione 5.0 ex-post** → blue "45 gg rimasti" badge (far).
4. Click **"Apri pratica"** on any row → navigates to the correct practice.

**Pass** Three rows, correct colour coding, correct link targets.

**Fail** Zero rows → `practice_deadlines` RLS or the query is missing the
tenant filter. Wrong badges → `daysUntil()` calculation wrong or `due_at`
stored in wrong timezone.

---

### Flow 7 — Settings → Dati legali (1 min)

**Steps**
1. Navigate to `/settings/legal` (or Settings → Dati Legali tab).
2. Confirm pre-filled values from the seeder:
   - Codice Fiscale: `98765432101`
   - N. CCIAA: `MI-9876543`
   - Responsabile Tecnico: `Giovanni Mancini`
3. Edit one field, Save → toast appears.
4. Hard-refresh → value persists.

**Pass** Save returns 200, value persists after refresh.

**Fail** 422 → check which `missing_tenant_fields` the API returns; a
required column may be NOT NULL in a later migration.

---

### Flow 8 — Lead Portal public slug (3 min)

**Steps**
1. Open the lead detail for any "opened" or "clicked" lead (e.g. Ceramiche
   Emiliane).
2. Find the **public_slug** in the URL or the lead card.
3. Open `https://<portal-url>/l/<slug>` in a separate incognito window.
4. Confirm: company name, kWp, GIF/image renders (or placeholder), ROI
   numbers visible.
5. Click any CTA on the portal → `outreach_clicked_at` updates on the
   dashboard lead (may take ≤10s).

**Pass** Portal renders without a 404. Click event recorded.

**Fail** 404 on portal → `public_slug` not stored or portal env var
`NEXT_PUBLIC_SUPABASE_URL` misconfigured. Event not recorded → portal
`/v1/portal/events` endpoint unreachable (check CORS).

---

## Full test — 60 minutes · Flows 1–24

> Complete flows 1–8 first, then continue below.

---

### Flow 9 — Preventivo (PDF download) (3 min)

**Steps**
1. Open the lead detail for Tessile Campana (closed_won).
2. Find the **Preventivo** section → confirm `DEMO/2026/0001` is listed
   with status "Emesso".
3. Click **"Scarica PDF"** → browser downloads or opens the PDF.
4. Visually verify: logo, ragione sociale, kWp, prezzo totale €57.640.

**Pass** PDF opens; main numbers match the seeder values.

**Fail** `pdf_url` is null → preventivo was seeded without a PDF (expected
when `--full-render` was not passed). Generate one manually:
`POST /v1/leads/{id}/quote` with `regenerate=true`, or re-run
`pnpm seed:demo:full`.

---

### Flow 10 — Crea pratica from lead detail (4 min)

**Steps**
1. Find a lead with `feedback = null` and `pipeline_status = appointment`.
   Set feedback to "Contratto firmato" via the feedback dropdown.
2. After save, the header should show the **"Crea pratica GSE"** button.
3. Click it → modal/page with prefill from `lead_quote`.
4. Verify prefilled fields: potenza kWp, pannelli, distributore.
5. Fill in catastali (foglio/particella) → Submit.
6. Toast: "Pratica XXXX/2026/0002 creata…" → redirect to `/practices/{id}`.

**Pass** New practice row appears in `/practices`; `lead.feedback` is
`contract_signed`.

**Fail** Button missing → `feedback` enum value didn't save or the
dashboard condition `lead.feedback === 'contract_signed'` not met.
Form submit 422 → check `missing_tenant_fields` (responsabile tecnico may
be required for DM 37/08).

---

### Flow 11 — Practice document status change (3 min)

**Steps**
1. Open any practice's detail page.
2. Find the `schema_unifilare` document in state **Bozza**.
3. Click **"Marca come revisionato"** → status chip changes to "Revisionato".
4. Click **"Marca come inviato"** → status changes to "Inviato".
5. Check `/scadenze` — if the template has a SLA, a new deadline should
   appear (check `modello_unico_p1` → Sent triggers `modello_unico_p2_due_30d`).

**Pass** Status persists after page refresh. Deadline appears when expected.

**Fail** 409 Conflict → document `UNIQUE(practice_id, template_code)` violation.
No deadline → `practice_deadlines_service.project_event_to_deadlines` not called
from the PATCH route.

---

### Flow 12 — Practice document regenerate (2 min)

**Steps**
1. Click **"Rigenera"** on any document with `status = reviewed`.
2. Confirm toast "Generazione in corso…".
3. Worker picks up the task and sets `status = draft` temporarily, then
   `status = reviewed` with updated `generated_at`.
4. If `--full-render` was used, a new `pdf_url` appears after ~30s.

**Pass** `generated_at` timestamp updates; no error in worker logs.

**Fail** Worker not running → check arq is connected to the correct Redis
URL. `practice_render_document_task` not registered → check
`apps/api/src/workers/main.py`.

---

### Flow 13 — Demo pipeline geocode preview (2 min)

**Steps**
1. Navigate to `/leads`.
2. The demo banner should be visible (requires `is_demo=true` on the
   tenant and `demo_pipeline_test_remaining > 0`).
3. Click **"Avvia test pipeline"** → dialog opens.
4. In the address field, type: `Via Toledo 256, Napoli`.
5. Tab out (blur) — a green pin and "Indirizzo riconosciuto" badge appear.
6. No attempt counter is decremented (verify by checking tenant row).

**Pass** Map pin shown, `GET /v1/demo/geocode-preview` returns `found=true`.

**Fail** Banner missing → `is_demo` flag or `demo_pipeline_test_remaining`
not set. Geocode fails → `MAPBOX_ACCESS_TOKEN` env var missing or expired.

---

### Flow 14 — Demo pipeline full run (8 min)

**Steps**
1. Fill the dialog with:
   - Ragione sociale: `Multilog Spa`
   - P.IVA: `09881610019` (seeded in mock enrichment)
   - Indirizzo: `Agglomerato ASI Pascarola, 80023 Caivano NA`
   - Nome DM: `Andrea Esposito`, Ruolo: `CEO`
   - Email destinatario: your QA inbox
2. Submit → dialog shows "Analisi in corso…"
3. Poll status in `demo_pipeline_runs`:
   - `scoring` → `creative` → `outreach` → `done`
4. Toast: "Lead creato!" with deep link to `/leads/{id}`.
5. Check QA inbox — outreach email arrives with Multilog personalisation,
   rendered image, ROI numbers, CTA button.
6. Verify attempt counter decremented by 1 (was 999, now 998).

**Pass** Email arrives in QA inbox within 90s of submit. `demo_pipeline_runs.status = done`.

**Fail** `creative` step fails → check `VIDEO_RENDERER_URL` and
`REPLICATE_API_TOKEN` (or `GOOGLE_SOLAR_API_KEY` if using Solar renderer).
`outreach` step fails → check Resend API key and `decision_maker_email_verified`.
Counter not decremented → RPC `demo_decrement_pipeline_attempts` needs
GRANT on service_role (migration 0077).

---

### Flow 15 — Demo pipeline attempt reset (1 min)

**Steps**
1. Via API or Postman:
   ```
   POST /v1/admin/demo/reset-attempts
   Authorization: Bearer <super_admin_jwt>
   { "tenant_id": "<uuid>", "count": 999 }
   ```
2. Response: `{ "ok": true, "attempts_remaining": 999 }`.
3. Check demo banner in dashboard — counter back to 999.

**Pass** Counter resets without touching tenant row's other fields.

**Fail** 403 → JWT not super_admin. 502 → RPC `demo_reset_pipeline_attempts`
missing (apply migration 0088).

---

### Flow 16 — Email tracking webhook chain (5 min)

**Steps**
1. From Flow 14, find the email in your QA inbox.
2. **Open** the email (remote images must load — this fires the pixel).
3. Wait 10–15s, then refresh the lead detail in the dashboard.
4. Verify `outreach_opened_at` is populated; timeline shows "Email aperta".
5. **Click** the CTA button in the email (links to the lead portal).
6. Verify `outreach_clicked_at` populated; `pipeline_status` advances
   to `clicked`.

**Pass** Both `*_at` timestamps populated within 30s of the action.

**Fail** Pixel not firing → check Resend tracking is enabled on the
sending domain (`tenant_email_domains.tracking_cname_verified_at`).
Events not landing → check `RESEND_WEBHOOK_SECRET` on Railway.

---

### Flow 17 — Lead portal → engagement (3 min)

**Steps**
1. From the clicked email, you're on the portal.
2. Scroll to the bottom of the portal page → `dashboard_visited_at` sets.
3. Play the GIF / video if present.
4. Fill in any contact form or reply CTA on the portal.
5. In dashboard, verify `pipeline_status = engaged` after the above
   interaction (may require manual feedback if automatic engagement rules
   aren't triggered).

**Pass** Portal renders fully; `dashboard_visited_at` set within 60s.

**Fail** Portal 404 → `NEXT_PUBLIC_SUPABASE_ANON_KEY` wrong on portal Vercel deployment.

---

### Flow 18 — Lead feedback workflow (2 min)

**Steps**
1. On any "engaged" lead, open the feedback panel.
2. Set feedback to **"Non raggiungibile"** → save → toast.
3. Refresh → chip shows "Non raggiungibile".
4. Change feedback to **"Qualificato"** → save.
5. Verify the lead status chip in the list updates accordingly.

**Pass** All three feedback transitions save and persist.

---

### Flow 19 — Quote creation (Preventivo) (4 min)

**Steps**
1. Open any lead with no existing quote and `feedback = contract_signed`.
   (Create one via Flow 10 if needed.)
2. Click **"Genera preventivo completo"** in the header.
3. Fill required manual fields: marca pannello, modello, prezzo netto.
4. Submit → PDF generation job enqueued.
5. After ~20s, `pdf_url` populated (or null if worker not running — check
   toast for job ID).
6. Click **"Scarica PDF"** → PDF opens; check branding, numbers, firma
   section.

**Pass** PDF generated with tenant logo, ragione sociale, kWp, price.

**Fail** 422 with `missing_tenant_fields` → run Flow 7 first (legal fields
required for DM 37/08 section). Worker not generating → check arq logs for
`quote_render_task`.

---

### Flow 20 — Admin: system health + stats (2 min)

**Steps**
1. Call:
   ```
   GET /v1/admin/system/health   → { "status": "ok" }
   GET /v1/admin/system/stats    → platform KPIs JSON
   ```
2. Verify `total_leads` ≥ 10 (seeder data).

**Pass** Both endpoints return 200 with non-empty data.

**Fail** 403 → JWT not super_admin. 502 → DB unreachable.

---

### Flow 21 — Admin: tenant management (2 min)

**Steps**
1. `GET /v1/admin/tenants` → list includes Demo Solar Srl.
2. `GET /v1/admin/tenants/{demo_tenant_id}` → full tenant row.
3. `PATCH /v1/admin/tenants/{demo_tenant_id}` with `{ "tier": "enterprise" }`
   → 200 OK.
4. Verify tier changed; change it back to `pro`.

**Pass** PATCH round-trips correctly without affecting other fields.

---

### Flow 22 — Admin: seed-test-candidate (smoke only, 2 min)

**Steps**
```bash
curl -X POST https://<api-url>/v1/admin/seed-test-candidate \
  -H "Authorization: Bearer <super_admin_jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "<demo_tenant_id>",
    "vat_number": "IT00000000000",
    "legal_name": "Test Smoke Factory Srl",
    "hq_address": "Via Rovigo 1", "hq_cap": "45100",
    "hq_city": "Rovigo", "hq_province": "RO",
    "hq_lat": 45.0699, "hq_lng": 11.7891,
    "decision_maker_email": "<qa-inbox>",
    "run_outreach": false
  }'
```

**Pass** 202 with `roof_id`, `subject_id`, `scoring_job_id`,
`scoring.score` between 0–100.

**Fail** 502 → check Solar mock mode (`GOOGLE_SOLAR_MOCK_MODE=true`) if
real key isn't available.

---

### Flow 23 — Settings: email domain verification (3 min)

**Steps**
1. Navigate to `/settings/email` or the email domains section.
2. Verify domain `agenda-pro.it` is listed with DNS verification status.
3. If not verified: click **"Verifica DNS"** → check Resend dashboard
   for the SPF/DKIM/DMARC records.

**Pass** Domain shown; verification status visible (may be "non verificato"
in staging — that's acceptable as long as the UI renders).

**Fail** Page blank → route not mounted or `tenant_email_domains` RLS
misconfigured.

---

### Flow 24 — Auth: session guard + logout (1 min)

**Steps**
1. While logged in, open `/leads` — renders correctly.
2. Log out → redirected to `/login`.
3. Try to access `/leads` directly → redirected to `/login` (not a 500).
4. Log back in → `/leads` loads.

**Pass** All four states work without a white-screen crash.

**Fail** Direct URL access returns 500 instead of redirect → Next.js
middleware not intercepting unauthenticated requests.

---

## Full checklist (print-and-tick)

```
Smoke (15 min)
  [ ] F01  Dashboard loads — 10 leads visible
  [ ] F02  Leads filters — chips change row count
  [ ] F03  Lead detail — score, ROI, anagrafica, timeline
  [ ] F04  Pratiche list — DEMO/2026/0001 visible
  [ ] F05  Pratica detail — 9 docs, mixed statuses
  [ ] F06  Scadenze — 3 deadlines, correct colours
  [ ] F07  Settings Dati legali — save persists
  [ ] F08  Lead Portal — renders, click event recorded

Full (additional 45 min)
  [ ] F09  Preventivo PDF — download + visual check
  [ ] F10  Crea pratica from lead feedback
  [ ] F11  Document status change → deadline triggered
  [ ] F12  Document regenerate → generated_at updates
  [ ] F13  Demo geocode preview — no attempt consumed
  [ ] F14  Demo full pipeline run → email in QA inbox
  [ ] F15  Demo reset-attempts → counter back to 999
  [ ] F16  Email tracking — opened + clicked timestamps
  [ ] F17  Lead portal engagement → dashboard_visited_at
  [ ] F18  Lead feedback — all enum values save
  [ ] F19  Quote creation → PDF rendered
  [ ] F20  Admin health + stats — 200 OK
  [ ] F21  Admin tenant PATCH — tier round-trip
  [ ] F22  Admin seed-test-candidate — 202 + score
  [ ] F23  Settings email domain — status visible
  [ ] F24  Auth session guard — logout redirects
```

---

## Known limitations (non-blocking for go-to-market)

### No Atoka contract yet
The full discovery pipeline (Funnel L1 → L4) cannot be smoke-tested
without a live Atoka API key. Enable `ATOKA_MOCK_MODE=true` to generate
synthetic companies locally. The demo pipeline (`/v1/demo/test-pipeline`)
bypasses Atoka entirely via `demo_mock_enrichment`.

### PDF rendering requires WeasyPrint
Practice document PDFs are generated server-side with WeasyPrint. On the
Railway free-tier container WeasyPrint is available; on local dev you need
to install it (`pip install weasyprint`) and its system deps (Cairo,
Pango). Without it, `pdf_url` stays null — the UI handles this gracefully
with a "Generazione in corso" spinner.

### WhatsApp not testable in QA
360dialog webhook requires a verified business number. WhatsApp flows
(Flows 11, 16b) require a phone with the test number saved. Skip these
in smoke runs.

### GIF rendering requires Remotion sidecar
`rendering_gif_url` is null when `VIDEO_RENDERER_URL` isn't pointed at a
running Remotion instance. The creative agent and portal fall back to the
static before/after image — emails still deliver, just without animation.

### Email tracking may lag up to 60s
Resend webhooks are delivered with up to 60s delay in staging. If
`outreach_opened_at` doesn't appear within 30s, wait another 30s before
calling the flow failed.

### Postal (Pixart) not in scope
Physical letter delivery requires a Pixart account and a non-zero postal
budget. The `postal_outreach` channel is wired but never triggered by
demo data. Skip postal-specific flows entirely.

### Demo practice documents have no real PDFs
`pnpm seed:demo:reset` creates documents with `pdf_url = null`. To get
real PDFs, run `pnpm seed:demo:full` (enqueues render tasks) and wait
for the arq worker to process them (~30s each). The "Scarica PDF" button
in the UI only appears when `pdf_url` is non-null.

---

## Resetting for the next run

```bash
# Wipe Demo Solar data and re-seed from scratch:
pnpm seed:demo:reset

# Reset demo attempt counter (after Flow 14 consumed one):
curl -X POST https://<api-url>/v1/admin/demo/reset-attempts \
  -H "Authorization: Bearer <super_admin_jwt>" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"<demo_tenant_id>","count":999}'
```

---

## Environment variables checklist

Verify all of these are set in Railway before starting the full test:

```
# Core
SUPABASE_SERVICE_ROLE_KEY
NEXT_PUBLIC_SUPABASE_URL
NEXT_PUBLIC_SUPABASE_ANON_KEY
JWT_SECRET
APP_ENV=production   (or staging)

# AI + Rendering
ANTHROPIC_API_KEY
GOOGLE_SOLAR_API_KEY   (or GOOGLE_SOLAR_MOCK_MODE=true)
MAPBOX_ACCESS_TOKEN

# Email
RESEND_API_KEY
RESEND_WEBHOOK_SECRET
RESEND_INBOUND_SECRET
DEMO_EMAIL_RECIPIENT_OVERRIDE=qa@agenda-pro.it   # for visual QA

# Storage
R2_ENDPOINT / R2_ACCESS_KEY / R2_SECRET_KEY / CDN_BASE_URL

# Optional (skip flows that need them if absent)
ATOKA_API_KEY          # F16 Funnel L1–L4 (use ATOKA_MOCK_MODE=true without it)
REPLICATE_API_TOKEN    # GIF rendering (optional — falls back to static image)
VIDEO_RENDERER_URL     # Remotion sidecar (optional)
DIALOG360_API_KEY      # WhatsApp (skip if absent)
PIXART_API_KEY         # Postal (skip if absent)
```

---

*Last updated: 2026-05-01 · Sprint C*
