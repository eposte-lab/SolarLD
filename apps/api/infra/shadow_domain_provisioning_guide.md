# SolarLead Shadow Domain Provisioning Guide

**Generated:** 2026-04-26
**Domains:** 4
**Total mailboxes:** 12
**Steady-state send capacity:** 600 emails / day
**Warm-up duration:** 21 days per inbox
**Day-1 send capacity:** 120 emails / day

---

## Overview

This guide walks through setting up 4 Google Workspace
outreach domains with 12 shared mailboxes (3 per domain).
Each inbox is enrolled in Smartlead.ai automated warm-up.

Architecture:
- **Brand domain** (`solarlead.it`) — used by Resend for transactional
  email (auth, notifications, lead portal). **Never used for cold outreach.**
- **Shadow domains** (below) — dedicated Google Workspace domains used
  exclusively for B2B cold outreach. Reputation isolation: if one domain
  has a delivery issue it doesn't affect the brand or the other domains.

Total time to complete setup: **~90 minutes** (mostly waiting for DNS propagation).

---

## Prerequisites

- [ ] Access to the DNS control panel for all 4 domains
- [ ] A Google Workspace Business Starter account per domain (€6/user/month)
- [ ] A Smartlead.ai account with API key in `.env` as `SMARTLEAD_API_KEY`
- [ ] `APP_SECRET_KEY` set in `.env` (Fernet key — `Fernet.generate_key()`)
- [ ] DMARC reporting mailbox `dmarc@solarlead.it` is receiving email

---

## Step 1 — Register Google Workspace for each domain

For each of the 4 domains below:

1. Go to <https://workspace.google.com/business/signup>
2. Enter the domain name when prompted
3. Complete the admin account setup. The admin account email is for
   administrative access only — **do NOT use it for outreach**.
4. Follow the Google Workspace domain verification steps (usually a
   TXT record at `@` — this is separate from DKIM verification).
5. Accept the billing; ~€6/user/month × 3 users per domain = ~€18/domain/month.

> 💡 Use a single Google billing account across all 4 domains to simplify invoicing.

---

## Step 2 — Create the mailboxes

In **Google Admin Console** (`admin.google.com`) for each domain, create
exactly 3 user accounts matching the personas below. Use the display names
exactly — they will appear in the "From:" field of every outreach email.


### solarlead-progetti.it

*Progetti fotovoltaici (focus manifatturiero / PMI)*

| Email | Display Name | Suggested password |
|-------|-------------|-------------------|
| `luca.ferrari@solarlead-progetti.it` | Luca Ferrari | `Luca#Solar2026!` *(change this)* |
| `giulia.romano@solarlead-progetti.it` | Giulia Romano | `Giulia#Solar2026!` *(change this)* |
| `marco.esposito@solarlead-progetti.it` | Marco Esposito | `Marco#Solar2026!` *(change this)* |

### solarlead-energia.it

*Energia rinnovabile (focus logistica / grande distribuzione)*

| Email | Display Name | Suggested password |
|-------|-------------|-------------------|
| `sara.bianchi@solarlead-energia.it` | Sara Bianchi | `Sara#Solar2026!` *(change this)* |
| `andrea.conti@solarlead-energia.it` | Andrea Conti | `Andrea#Solar2026!` *(change this)* |
| `chiara.ricci@solarlead-energia.it` | Chiara Ricci | `Chiara#Solar2026!` *(change this)* |

### info-solarlead.it

*Info / discovery (primo contatto generico)*

| Email | Display Name | Suggested password |
|-------|-------------|-------------------|
| `matteo.russo@info-solarlead.it` | Matteo Russo | `Matteo#Solar2026!` *(change this)* |
| `elena.marino@info-solarlead.it` | Elena Marino | `Elena#Solar2026!` *(change this)* |
| `davide.greco@info-solarlead.it` | Davide Greco | `Davide#Solar2026!` *(change this)* |

### pro-solarlead.com

*Pro (.com) — targeting aziende internazionalizzate / export*

| Email | Display Name | Suggested password |
|-------|-------------|-------------------|
| `sofia.lombardi@pro-solarlead.com` | Sofia Lombardi | `Sofia#Solar2026!` *(change this)* |
| `roberto.fontana@pro-solarlead.com` | Roberto Fontana | `Roberto#Solar2026!` *(change this)* |
| `valentina.caruso@pro-solarlead.com` | Valentina Caruso | `Valentina#Solar2026!` *(change this)* |

> 🔒  Store the actual passwords in 1Password / Bitwarden under the
> "SolarLead Outreach Inboxes" vault. Never commit them to git.

---

## Step 3 — Configure DNS records

Set DNS TTL to **300 seconds** on all records before you start.
Lower TTL = faster iteration when debugging DNS issues.

Add the records below at your DNS provider **for each domain**.
After adding all records, run the SolarLead DNS verification endpoint:

```bash
curl -X POST https://api.solarld.app/v1/email-domains/{domain-id}/dns-check
```

Or from the dashboard: **Settings → Email Domains → Verify now**.



### solarlead-progetti.it

*Progetti fotovoltaici (focus manifatturiero / PMI)*


**MX Records** (Google Workspace)

```
Priority    Value
--------------------------------------------------
1           ASPMX.L.GOOGLE.COM.
5           ALT1.ASPMX.L.GOOGLE.COM.
5           ALT2.ASPMX.L.GOOGLE.COM.
10          ALT3.ASPMX.L.GOOGLE.COM.
10          ALT4.ASPMX.L.GOOGLE.COM.
```

**SPF TXT** (host: `@`)

```
v=spf1 include:_spf.google.com ~all
```

**DKIM TXT** (host: `google._domainkey.solarlead-progetti.it`)

```
v=DKIM1; k=rsa; p=<PASTE_KEY_FROM_GOOGLE_ADMIN_CONSOLE>
```
> ⚠️  DKIM key must be generated in Google Admin Console. See Step 3 below.

**DMARC TXT** (host: `_dmarc.solarlead-progetti.it`)

```
v=DMARC1; p=none; rua=mailto:dmarc@solarlead.it; ruf=mailto:dmarc@solarlead.it; pct=100; adkim=s; aspf=s
```

**Tracking CNAME** (host: `go.solarlead-progetti.it`)

```
go.solarlead-progetti.it  CNAME  track.solarld.app.
```

**Mailboxes to create in Google Admin Console**

| Email | Display Name | Persona |
|-------|-------------|---------|
| `luca.ferrari@solarlead-progetti.it` | Luca Ferrari | Responsabile Sviluppo Progetti |
| `giulia.romano@solarlead-progetti.it` | Giulia Romano | Consulente Impianti Industriali |
| `marco.esposito@solarlead-progetti.it` | Marco Esposito | Tecnico Fotovoltaico Senior |


### solarlead-energia.it

*Energia rinnovabile (focus logistica / grande distribuzione)*


**MX Records** (Google Workspace)

```
Priority    Value
--------------------------------------------------
1           ASPMX.L.GOOGLE.COM.
5           ALT1.ASPMX.L.GOOGLE.COM.
5           ALT2.ASPMX.L.GOOGLE.COM.
10          ALT3.ASPMX.L.GOOGLE.COM.
10          ALT4.ASPMX.L.GOOGLE.COM.
```

**SPF TXT** (host: `@`)

```
v=spf1 include:_spf.google.com ~all
```

**DKIM TXT** (host: `google._domainkey.solarlead-energia.it`)

```
v=DKIM1; k=rsa; p=<PASTE_KEY_FROM_GOOGLE_ADMIN_CONSOLE>
```
> ⚠️  DKIM key must be generated in Google Admin Console. See Step 3 below.

**DMARC TXT** (host: `_dmarc.solarlead-energia.it`)

```
v=DMARC1; p=none; rua=mailto:dmarc@solarlead.it; ruf=mailto:dmarc@solarlead.it; pct=100; adkim=s; aspf=s
```

**Tracking CNAME** (host: `go.solarlead-energia.it`)

```
go.solarlead-energia.it  CNAME  track.solarld.app.
```

**Mailboxes to create in Google Admin Console**

| Email | Display Name | Persona |
|-------|-------------|---------|
| `sara.bianchi@solarlead-energia.it` | Sara Bianchi | Energy Manager |
| `andrea.conti@solarlead-energia.it` | Andrea Conti | Consulente Risparmio Energetico |
| `chiara.ricci@solarlead-energia.it` | Chiara Ricci | Responsabile Efficienza Energetica |


### info-solarlead.it

*Info / discovery (primo contatto generico)*


**MX Records** (Google Workspace)

```
Priority    Value
--------------------------------------------------
1           ASPMX.L.GOOGLE.COM.
5           ALT1.ASPMX.L.GOOGLE.COM.
5           ALT2.ASPMX.L.GOOGLE.COM.
10          ALT3.ASPMX.L.GOOGLE.COM.
10          ALT4.ASPMX.L.GOOGLE.COM.
```

**SPF TXT** (host: `@`)

```
v=spf1 include:_spf.google.com ~all
```

**DKIM TXT** (host: `google._domainkey.info-solarlead.it`)

```
v=DKIM1; k=rsa; p=<PASTE_KEY_FROM_GOOGLE_ADMIN_CONSOLE>
```
> ⚠️  DKIM key must be generated in Google Admin Console. See Step 3 below.

**DMARC TXT** (host: `_dmarc.info-solarlead.it`)

```
v=DMARC1; p=none; rua=mailto:dmarc@solarlead.it; ruf=mailto:dmarc@solarlead.it; pct=100; adkim=s; aspf=s
```

**Tracking CNAME** (host: `go.info-solarlead.it`)

```
go.info-solarlead.it  CNAME  track.solarld.app.
```

**Mailboxes to create in Google Admin Console**

| Email | Display Name | Persona |
|-------|-------------|---------|
| `matteo.russo@info-solarlead.it` | Matteo Russo | Specialista Fotovoltaico |
| `elena.marino@info-solarlead.it` | Elena Marino | Consulente Energie Rinnovabili |
| `davide.greco@info-solarlead.it` | Davide Greco | Account Manager Solare |


### pro-solarlead.com

*Pro (.com) — targeting aziende internazionalizzate / export*


**MX Records** (Google Workspace)

```
Priority    Value
--------------------------------------------------
1           ASPMX.L.GOOGLE.COM.
5           ALT1.ASPMX.L.GOOGLE.COM.
5           ALT2.ASPMX.L.GOOGLE.COM.
10          ALT3.ASPMX.L.GOOGLE.COM.
10          ALT4.ASPMX.L.GOOGLE.COM.
```

**SPF TXT** (host: `@`)

```
v=spf1 include:_spf.google.com ~all
```

**DKIM TXT** (host: `google._domainkey.pro-solarlead.com`)

```
v=DKIM1; k=rsa; p=<PASTE_KEY_FROM_GOOGLE_ADMIN_CONSOLE>
```
> ⚠️  DKIM key must be generated in Google Admin Console. See Step 3 below.

**DMARC TXT** (host: `_dmarc.pro-solarlead.com`)

```
v=DMARC1; p=none; rua=mailto:dmarc@solarlead.it; ruf=mailto:dmarc@solarlead.it; pct=100; adkim=s; aspf=s
```

**Tracking CNAME** (host: `go.pro-solarlead.com`)

```
go.pro-solarlead.com  CNAME  track.solarld.app.
```

**Mailboxes to create in Google Admin Console**

| Email | Display Name | Persona |
|-------|-------------|---------|
| `sofia.lombardi@pro-solarlead.com` | Sofia Lombardi | Solar Solutions Consultant |
| `roberto.fontana@pro-solarlead.com` | Roberto Fontana | Business Development Manager |
| `valentina.caruso@pro-solarlead.com` | Valentina Caruso | Senior PV Project Advisor |

---

## Step 4 — Generate and install DKIM keys

For **each domain**:

1. In Google Admin Console → **Apps → Google Workspace → Gmail → Authenticate email**
2. Select the domain from the dropdown
3. Click **Generate new record** → selector prefix: `google` → key size: **2048 bit**
4. Copy the TXT record value (it looks like `v=DKIM1; k=rsa; p=MIIBIjANBg…`)
5. Add/update the DNS TXT record:
   - Host: `google._domainkey.{your-domain}`
   - Value: paste the full string from step 4
6. Back in Google Admin Console, click **Start authentication**
7. Wait for Google to verify (usually <10 minutes after DNS propagates)
8. Status should show "Email authenticated" ✅

> ⚠️  Do **not** start sending email from any inbox until DKIM shows
> "Email authenticated". Sending without DKIM is the #1 spam trigger.

---

## Step 5 — Connect each inbox via Gmail OAuth in SolarLead

Once Google Workspace is configured and DKIM is verified:

1. Open SolarLead Dashboard → **Settings → Inboxes**
2. For each of the 12 mailboxes, click **"+ Add inbox"** then
   **"Connect Gmail"**
3. Authenticate with the mailbox credentials (e.g. `luca.ferrari@solarlead-progetti.it`)
4. Grant the `https://www.googleapis.com/auth/gmail.send` scope
5. SolarLead stores the refresh token encrypted; the inbox status
   should switch to **"Gmail OAuth ✅"**

Alternatively, enroll via the CLI (runs `smartlead_service.enroll_all_from_topology()`):

```bash
python -m src.scripts.shadow_domain_setup --enroll
```

This reads `shadow_domains_topology.json` and calls the Smartlead API to
create and warm-up all 12 inboxes automatically.

---

## Step 6 — Enroll in Smartlead warm-up

Once all inboxes are OAuth-connected in SolarLead:

```bash
# Enroll all inboxes in Smartlead warm-up (requires SMARTLEAD_API_KEY in .env)
python -m src.services.smartlead_service enroll-all
```

Expected output:
```
✅  luca.ferrari@solarlead-progetti.it → enrolled (id=12345)
✅  giulia.romano@solarlead-progetti.it → enrolled (id=12346)
… (12 lines total)
```

Warm-up schedule (per inbox):
| Days  | Warmup emails/day | Live outreach cap |
|-------|-------------------|-------------------|
| 1–7   | 10                | 10                |
| 8–14  | 25                | 25                |
| 15–21 | 40                | 40                |
| 22+   | 40 (maintenance)  | **50** (steady state) |

Total day-1 capacity: **120 emails / day** across all 12 inboxes.
Total steady-state capacity: **600 emails / day** across all 12 inboxes.
Target SLA ("250 in-target / day"): reached on **day 8** of warm-up.

---

## Step 7 — Monitor DMARC reports

After 24 hours of sending, check `dmarc@{BRAND_DOMAIN}` for DMARC
aggregate reports (`.xml.gz` attachments from Google, Yahoo, Microsoft).

Key metrics to watch (first 14 days):
- `dkim=pass` rate should be **100%**
- `spf=pass` rate should be **100%**
- `disposition=none` for all rows (p=none mode = report only, no quarantine)

After 14 days of clean reports, upgrade DMARC policy to `p=quarantine`:
```
v=DMARC1; p=quarantine; rua=mailto:{DMARC_RUA}; pct=100; adkim=s; aspf=s
```

After 30 more days of clean reports, upgrade to `p=reject`.

---

## Step 8 — First live send

Before the first live send:

- [ ] All 4 domains show DKIM "Email authenticated" in Google Admin
- [ ] All DNS records verified green in SolarLead dashboard
- [ ] All 12 inboxes connected via OAuth in SolarLead
- [ ] All 12 inboxes enrolled in Smartlead warm-up (status: active)
- [ ] `APP_SECRET_KEY` configured in API `.env`
- [ ] At least 3 warm-up days completed (inbox health > 80% in Smartlead)

Then flip the first tenant to V2 pipeline:
```sql
UPDATE tenants SET pipeline_version = 2 WHERE id = '<tenant-uuid>';
```

Monitor the first 50 sends in Sentry + dashboard `/invii` before
scaling up.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Gmail → Spam on day 1 | DKIM not verified yet | Verify DKIM first |
| High bounce rate (>5%) | Bad prospect list | Clean list; pause domain |
| Smartlead enrollment fails | Wrong SMTP password | Re-check App Password |
| `InvalidToken` on unsubscribe | `APP_SECRET_KEY` not set | Set env var |
| Domain paused automatically | Bounce/complaint threshold | Check `domain_reputation` table |

---

*Generated by `src/scripts/shadow_domain_setup.py` — edit `SHADOW_DOMAINS` in that file to update.*
