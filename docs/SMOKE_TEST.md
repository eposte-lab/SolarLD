# Smoke Test Protocol

15 checkpoints, one fresh tenant, one session. If every step passes in a
single continuous run the platform is **production-shaped** for the
first beta installer. Expect 2–3 full runs to shake out flakes.

Time budget: **~45 min** per clean run once you have the fixtures ready.

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

Expect: new row within 5s, `status='active'`, `wizard_completed_at` set
in `tenant_configs`.

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

## Exit criteria

Staging is smoke-green when **all 15 checkpoints pass in a single
continuous run on a fresh tenant, within 60 minutes, without manual
intervention between steps**.

Flakes to investigate before shipping:
- Any step that needs to be retried more than once.
- Any toast that fires but no corresponding `events` row materialises
  within 2× the stated budget.
- Any worker log showing `Task failed` for a job linked to this run.

When clean: record the run in `docs/SMOKE_TEST.md` as an appendix note
(date + commit SHA) and share the dashboard URL with your first beta
installer.
