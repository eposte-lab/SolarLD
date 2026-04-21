# Smoke Test Protocol v2

**23 checkpoints**, one fresh tenant, one session. The first 15 steps
cover the core B2B funnel + outreach + CRM surfaces (same as the v1
protocol). Steps 16–23 are new in v2 and cover the **B2B Funnel 4-level
rewrite**, the **modular wizard**, and the **B2C residential** pipeline
(Meta Lead Ads, door-to-door export, inverted post-engagement Solar
qualification). If every step passes in a single continuous run the
platform is **production-shaped** for the first beta installer. Expect
2–3 full runs to shake out flakes.

Time budget: **~75 min** per clean run on v2 (15 min extra over v1 for
the new funnel + B2C flows).

---

## Preconditions

Before you start the timer:

- [ ] `docs/STAGING.md` runbook finished — API + worker healthy on
      Railway, dashboard + portal deployed on Vercel, all 4 webhook
      sanity curls return 401.
- [ ] Test tenant seeded (`scripts/seed_test_tenant.py` OR onboarding
      wizard). Note the tenant UUID — several steps need it.
- [ ] Admin user loggable into dashboard; you know the password.
- [ ] Disposable inbox with remote-image loading enabled (Gmail alias
      `you+solarlead@gmail.com` works; Mailtrap works too).
- [ ] Phone with WhatsApp able to send to the 360dialog business number.
- [ ] Two terminal tabs: one tailing Railway worker logs, one with
      `psql` or the Supabase SQL editor ready.

If any of those is missing, stop and fix it — the protocol assumes all
external surfaces are up.

---

## Checkpoint matrix

Fail-fast: if step N fails, stop. Don't chase downstream symptoms
before N is fixed. Each step lists the acceptance check and the single
most useful place to look when it fails.

| # | Flow | Acceptance | Budget | First place to look on failure |
|---|------|-----------|--------|--------------------------------|
| 1 | Tenant creation | Tenant row + wizard `completed_at` set | 5 min | API logs `tenant_config.wizard_upsert` |
| 2 | Territory scan | ≥10 rows in `/leads` | 30s | Worker logs `hunter_task` |
| 3 | Lead scoring | `score` 0–100, non-null | 10s | Worker logs `scoring_task` |
| 4 | Creative | ≥2 variants, "Invia" enabled | 20s | Worker logs `creative_task`, Anthropic quota |
| 5 | Outbound email | Email arrives | 30s | Resend dashboard → Logs |
| 6 | Delivered webhook | Toast + `events` row | 60s | Resend webhook delivery panel |
| 7 | Open tracking | `lead.email_opened` event | 30s | Resend → pixel → ensure remote images loaded |
| 8 | Click tracking | `lead.email_clicked` + portal opens | 10s | Resend → link tracking enabled on template |
| 9 | Portal engagement | `portal_events` populated | 60s | `apps/lead-portal` console + `/v1/portal/events` network tab |
| 10 | Reply inbound | Toast with sentiment | 45s | Resend inbound webhook deliveries; `RESEND_INBOUND_SECRET` |
| 11 | WhatsApp inbound | ConversationAgent replies | 60s | 360dialog delivery logs + `DIALOG360_WEBHOOK_SECRET` |
| 12 | Pixart postal | `lead.postal_delivered` | manual | Signature header matches `PIXART_WEBHOOK_SECRET` |
| 13 | A/B experiments | Per-variant open-rate updates | 60s | `template_experiments` rows + dashboard `/experiments` |
| 14 | CRM outbound | `crm_webhook_deliveries` row status=200 | 60s | Customer CRM endpoint + webhook secret |
| 15 | Auth guard | Logout → `/dashboard/*` redirects to `/login` | instant | Supabase Auth + middleware cookie |
| 16 | Funnel L1 (Atoka) | ≥50 rows in `scan_candidates_l1` | 60s | Worker logs `l1_discovery`, Atoka quota |
| 17 | Funnel L2 (enrichment) | ≥30% rows with non-null phone | 90s | Worker logs `l2_enrichment`, Places quota |
| 18 | Funnel L3 (proxy score) | ≥10 records with `score ≥ 70` | 120s | Worker logs `l3_proxy_score`, Claude Haiku quota |
| 19 | Funnel L4 (Solar gate) | `leads` ≤ candidates × 20% | 60s | Worker logs `l4_solar_gate`, Solar quota |
| 20 | Modular wizard | 5 rows in `tenant_modules` for fresh tenant | 5 min | API logs `modules.upsert` |
| 21 | B2C audience | ≥1 row in `b2c_audiences` | 30s | Worker logs `b2c_residential_*` |
| 22 | B2C Meta campaign | `trigger_meta_campaign` → `queued_stub` | 10s | API logs `b2c_outreach.meta_campaign.stub` |
| 23 | B2C post-engagement Solar | Positive reply → `leads.roof_id` set | 90s | Worker logs `b2c_qualify.complete` |

---

## Step-by-step

### 1. Tenant creation

Either:
- **Wizard** — open `https://dashboard-staging.<domain>/onboarding` in
  incognito, walk through all 6 steps (Step 6 integrazioni is optional
  — fill or skip). Submit.
- **Seed script** — `cd apps/api && .venv/bin/python scripts/seed_test_tenant.py`

Verify:

```sql
SELECT id, business_name, tier, status,
       settings ? 'neverbounce_api_key' AS has_nb_key
FROM tenants
ORDER BY created_at DESC
LIMIT 1;
```

Expect: new row within 5s, `status='active'`, and five rows in
`tenant_modules` (`module_key` in
`sorgente,tecnico,economico,outreach,crm`, each with `version >= 1`).

### 2. Territory scan

Navigate: `Territories` → existing territory (CAP 80100 if you used
the seed) → **Avvia scan**.

Within 30s, `/leads` shows ≥10 rows. If empty, the Hunter is
failing — tail the worker:

```bash
railway logs -s worker | rg hunter_task
```

### 3. Lead scoring

Open any lead from the list. The detail view shows `score` (0–100) and
a breakdown (technical / solvency / incentives / geo). If `score` is
`0` and breakdown is empty, the ScoringAgent didn't run — enqueue
manually from `/admin/jobs` (or check the worker for exceptions).

### 4. Creative generation

On the lead detail page, click **Genera creatività**. Wait ≤20s. Expect
≥2 template variants (e.g. "Diretto" and "Storytelling") and the
**Invia outreach** button becomes enabled.

### 5. Outbound email

Swap the lead's `decision_maker_email` to your disposable inbox:

```sql
UPDATE subjects SET decision_maker_email = 'you+smoke@gmail.com'
WHERE id = '<subject-id>';
```

Click **Invia outreach** → pick a variant. The email should arrive in
your inbox in ≤30s.

### 6. Delivered webhook

Back on the dashboard, watch for a toast titled **"Email consegnata"**
in the bottom-right corner. Cross-check:

```sql
SELECT event_type, created_at FROM events
WHERE lead_id = '<lead-id>' AND event_type = 'lead.email_delivered';
```

### 7. Open tracking

Open the email in the inbox, **load remote images** (Gmail may block
them by default). Within 30s: dashboard shows a new toast **"Email
aperta"**; `events` row `lead.email_opened` appears.

### 8. Click tracking

Click the **Scopri di più** link in the email. The lead portal
page opens. Within 10s, dashboard shows **"Click su link"** toast;
`events` gets `lead.email_clicked`.

### 9. Portal engagement

On the portal page, scroll to the bottom, hover the ROI section for
~20s, expand any accordion. Within 60s:

```sql
SELECT COUNT(*) FROM portal_engagement WHERE lead_id = '<lead-id>';
```

Expect > 0. The lead's `engagement_level` (on `leads`) should also
tick up from `none` → `viewed` → `engaged`.

### 10. Reply inbound

From your email client, reply to the outreach with:

> Mi interessa, vorrei più informazioni sul risparmio.

Within 45s:

```sql
SELECT sentiment, intent FROM lead_replies
WHERE lead_id = '<lead-id>'
ORDER BY received_at DESC LIMIT 1;
```

Expect `sentiment = 'positive'`, `intent` one of
`{information, appointment, pricing}`. A dashboard toast labelled
**"Risposta ricevuta"** should fire.

### 11. WhatsApp inbound

From your phone, send to the 360dialog business number:

```
SL-<lead.public_slug>
```

Within 60s a new `conversations` row appears and the ConversationAgent
sends an automatic reply to your phone. If nothing arrives, the
webhook is the suspect:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  "https://<api>/v1/webhooks/whatsapp?tenant_id=<tenant-id>"
# Expect 401 (missing signature), NOT 404 or 500.
```

### 12. Pixart postal

If the dashboard exposes a "Stampa cartolina" CTA, click it; otherwise
simulate the provider directly. Compute signature:

```bash
SECRET="$PIXART_WEBHOOK_SECRET"
BODY='{"tracking_code":"<postal-tracking-number>","event_type":"delivered"}'
SIG=$(printf %s "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | awk '{print $2}')

curl -X POST https://<api>/v1/webhooks/pixart \
  -H "Content-Type: application/json" \
  -H "X-Pixart-Signature: $SIG" \
  --data "$BODY"
# → {"ok":"enqueued"}
```

Then:

```sql
SELECT event_type FROM events
WHERE lead_id = '<lead-id>' AND event_type = 'lead.postal_delivered';
```

Expect one row. The dashboard toast reads **"Cartolina consegnata"**.

### 13. A/B experiments

Create an experiment from `/experiments` with 2 variants on the same
template. Send outreach to a different lead (not the one already used
— experiments gate on first-touch). Open both resulting emails.

```sql
SELECT variant_key, opens, clicks FROM template_experiment_variants
WHERE experiment_id = '<exp-id>';
```

Expect per-variant counts to update in real time on the
`/experiments/<id>` page.

### 14. CRM outbound

If a CRM webhook is configured (`/settings/integrations`), change
the lead's `pipeline_status` to `qualified` via the dashboard. Within
60s:

```sql
SELECT status_code, latency_ms FROM crm_webhook_deliveries
WHERE tenant_id = '<tenant-id>'
ORDER BY created_at DESC LIMIT 1;
```

Expect `status_code = 200`. If no CRM is configured, skip this step
— note "N/A" rather than marking it failed.

### 15. Auth guard

Log out from the dashboard. Paste `https://dashboard-staging.<domain>/leads`
directly into the URL bar. Expect instant redirect to `/login`. If the
page renders even briefly, the middleware guard is broken — fix before
sharing credentials with anyone.

---

## v2 checkpoints (16–23)

The next 8 steps exercise the v2 architecture introduced in April 2026:
the 4-level B2B funnel (Atoka → Places → Claude Haiku → Solar gate),
the modular wizard (`tenant_modules` table), and the B2C residential
pipeline (audiences, Meta Lead Ads, door-to-door export, inverted
Solar-after-engagement).

### 16. Funnel L1 — Atoka discovery

Trigger a `b2b_funnel_v2` scan from the dashboard (Territories →
`Avvia scan` → pick mode *Precision funnel v2*). A CAP-scoped
territory like **80100** is a good fixture.

```sql
SELECT count(*) FROM scan_candidates_l1
WHERE tenant_id = '<tenant-id>'
  AND scan_id = '<scan-id>';
```

Expect **≥50 rows** within 60s. If zero, Atoka creds are wrong or the
Sorgente module has no ATECO codes — check `tenant_modules`
(`module_key='sorgente'`).

### 17. Funnel L2 — enrichment

Wait for the L2 pass (Places Text Search + Details) to finish. Should
take ~60-90s for 500 candidates at 10 QPS.

```sql
SELECT count(*) FILTER (WHERE enrichment->>'phone' IS NOT NULL)
     ::float / count(*)::float AS phone_coverage
FROM scan_candidates_l1
WHERE scan_id = '<scan-id>';
```

Expect **≥0.3** (30% coverage is realistic — many Italian SMEs don't
list a phone on Google). <0.1 indicates the Places key is missing /
throttled / billing disabled.

### 18. Funnel L3 — proxy scoring

Wait for the L3 (Claude Haiku batch scoring) pass.

```sql
SELECT count(*) FILTER (WHERE score >= 70) AS high_score,
       count(*) AS total,
       avg(score)::int AS mean_score
FROM scan_candidates
WHERE scan_id = '<scan-id>' AND stage >= 3;
```

Expect **high_score ≥ 10**, mean 40–60. Zero high_scores with non-zero
total → the Claude response schema may have shifted; check worker
logs for `l3_proxy_score_parse_error`.

### 19. Funnel L4 — Solar gate on top 20%

```sql
SELECT (SELECT count(*) FROM leads WHERE scan_id = '<scan-id>') AS leads_out,
       (SELECT count(*) FROM scan_candidates
        WHERE scan_id = '<scan-id>' AND stage = 4) AS l4_checked;
```

Expect **`l4_checked ≈ total_candidates × config.tecnico.solar_gate_pct`**
(default 20%) and `leads_out ≤ l4_checked`. If `l4_checked` is the
entire candidate set, the gate is broken — confirm the ordering
by `score DESC` in `level4_solar_gate.py::select_top_n`.

Cost check: `/v1/scans/<scan-id>/costs` should report **< €5 total**
for a 500-candidate scan (≈10× cheaper than the v1 Places+Solar flow
that was retired in migration 0035).

### 20. Modular wizard (fresh tenant)

Log out. Sign up as a new test user; complete the modular wizard at
`/onboarding` (5 steps: Sorgente, Tecnico, Economico, Outreach, CRM).

```sql
SELECT module_key, active, config ? 'ateco_codes' AS sorgente_filled
FROM tenant_modules
WHERE tenant_id = '<new-tenant-id>'
ORDER BY module_key;
```

Expect **5 rows** (one per module_key) with at least `sorgente` and
`tecnico` non-empty. The wizard should let you skip Outreach/CRM and
still land at the dashboard — those rows exist with `active=false`
defaults.

### 21. B2C audience materialisation

On the new tenant, seed a CAP-scoped territory whose CAP is in
`geo_income_stats` (run `scripts/load_istat_income.py` at least once
in staging). Change the tenant's `sorgente` module to include
`reddito_min_eur=30000`. Trigger a `b2c_residential` scan.

```sql
SELECT count(*), sum(stima_contatti) AS total_contacts
FROM b2c_audiences
WHERE tenant_id = '<tenant-id>' AND scan_id = '<scan-id>';
```

Expect **≥1 row** with non-zero `stima_contatti`. The scan emits no
leads (`roofs_discovered=0`) — B2C defers qualification.

### 22. B2C Meta campaign (stub submission)

```bash
TOKEN=$(grep SUPABASE_ACCESS .env.staging | cut -d= -f2)
AUD=$(psql -At -c "SELECT id FROM b2c_audiences WHERE tenant_id='<tid>' LIMIT 1")
curl -s -X POST "https://<api>/v1/b2c/audiences/$AUD/meta-campaign" \
  -H "Authorization: Bearer $TOKEN" | jq .
# Expect: {"status":"queued_stub","audience_id":"<uuid>",...}
```

API log: `b2c_outreach.meta_campaign.stub` with matching tenant/audience.
Once the Meta app is approved, swapping the stub for a real Graph API
call is a one-file change — this step will flip from `queued_stub` to
`submitted`.

Also verify the door-to-door export responds:

```bash
curl -s -I -H "Authorization: Bearer $TOKEN" \
  "https://<api>/v1/b2c/audiences/$AUD/export.pdf" | head -3
# Expect: HTTP/1.1 200, Content-Type: application/pdf
```

### 23. B2C post-engagement Solar qualify

Synthesise an inbound Meta lead (via the webhook) then reply-positive
to it. Faster way: insert directly via service role:

```sql
INSERT INTO leads (tenant_id, source, public_slug, meta_lead_id,
                   pipeline_status, inbound_payload)
VALUES ('<tenant-id>', 'b2c_meta_ads',
        'meta_smoke_' || substring(md5(random()::text), 1, 8),
        'smoke_' || substring(md5(random()::text), 1, 10),
        'new',
        '{"full_address":"Via Roma 1","city":"Napoli","postcode":"80100"}');
```

Then POST a reply (or simulate):

```sql
INSERT INTO lead_replies (tenant_id, lead_id, from_email,
                          body_text, received_at)
VALUES ('<tenant-id>', '<lead-id>', 'owner@example.com',
        'Sì, sono interessato, richiamatemi.', now());
```

Enqueue the replies task manually via `/admin/jobs` or via:

```bash
curl -X POST https://<api>/v1/admin/enqueue -d '{"task":"replies_task",...}'
```

Within 90s:

```sql
SELECT roof_id, subject_id, pipeline_status, source
FROM leads WHERE id = '<lead-id>';
```

Expect `roof_id` NOT NULL, `pipeline_status='qualified'`,
`source='b2c_post_engagement'`. Worker log line:
`b2c_qualify.complete accepted=true kwp=...`.

---

## Exit criteria

Staging v2 is smoke-green when **all 23 checkpoints pass in a single
continuous run on a fresh tenant, within 80 minutes, without manual
intervention between steps**.

Flakes to investigate before shipping:
- Any step that needs to be retried more than once.
- Any toast that fires but no corresponding `events` row materialises
  within 2× the stated budget.
- Any worker log showing `Task failed` for a job linked to this run.

When clean: record the run in `docs/SMOKE_TEST.md` as an appendix note
(date + commit SHA) and share the dashboard URL with your first beta
installer.
