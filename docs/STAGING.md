# Staging Bring-Up Runbook

End-to-end steps to deploy SolarLead to staging. Assumes provisioned
accounts for Supabase, Railway, Vercel, Resend, 360dialog, NeverBounce,
and Pixart. Expect 2–3h of clicking once secrets are in hand.

---

## 0. Secret generation

All before anything else — paste these into a scratchpad (never commit):

```bash
openssl rand -hex 32   # JWT_SECRET
openssl rand -hex 32   # ENCRYPTION_KEY
openssl rand -hex 24   # RESEND_INBOUND_SECRET (URL-safe)
openssl rand -hex 32   # PIXART_WEBHOOK_SECRET (configured on Pixart side)
openssl rand -hex 32   # DIALOG360_WEBHOOK_SECRET (configured on 360dialog side)
```

The API refuses to start in `APP_ENV=staging` or `APP_ENV=production`
with any of the dev defaults still in place — enforced by
`Settings._validate_non_dev_env_secrets` in
`apps/api/src/core/config.py`. If startup fails, read the exception:
it lists every missing/invalid secret in one raise.

---

## 1. Supabase staging project

1. Create a new project `solarlead-staging` in the Supabase dashboard
   (EU region, matches production intent).
2. Link from a clone:
   ```bash
   supabase link --project-ref <staging-ref>
   ```
3. Apply migrations (28 including `0028_postal_tracking_index.sql`):
   ```bash
   supabase migration up
   ```
4. Diff check — must be empty:
   ```bash
   supabase db diff --linked
   ```
5. Realtime publication sanity:
   ```sql
   SELECT tablename FROM pg_publication_tables
   WHERE pubname = 'supabase_realtime';
   ```
   Expect at minimum: `events`, `lead_replies`, `conversations`.
6. Capture these values for later:
   - Anon key          → Vercel + Railway `NEXT_PUBLIC_SUPABASE_ANON_KEY`
   - Service role key  → Railway `SUPABASE_SERVICE_ROLE_KEY`
   - Project URL       → both   `NEXT_PUBLIC_SUPABASE_URL`
   - DB URL            → Railway `SUPABASE_DB_URL`
   - JWT secret        → Railway `SUPABASE_JWT_SECRET`

---

## 2. Railway — API + worker

One project, two services, both rooted at `apps/api`, sharing an env
group and a Redis addon.

| Service | Start command | Notes |
| ------- | ------------- | ----- |
| `api`   | `uvicorn src.main:app --host 0.0.0.0 --port $PORT` | Health check: `GET /health` |
| `worker`| `python -m src.workers.main` | No port; arq long-poll |

Provision Redis via the Railway addon or an external Upstash — set
`REDIS_URL` in the env group. The validator will refuse startup if it
still points at localhost.

Copy every key from `.env.staging.example` into the Railway env group,
replacing each `REPLACE_ME_*` with the real value. Deploy both
services. Expect a `arq worker starting` log line in the worker within
60s.

---

## 3. Vercel — dashboard + portal

Two projects in one monorepo:

| Vercel project         | Root              | Required env vars |
| ---------------------- | ----------------- | ----------------- |
| `solarlead-dashboard`  | `apps/dashboard`  | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_LEAD_PORTAL_URL`, `NEXT_PUBLIC_DASHBOARD_URL` |
| `solarlead-portal`     | `apps/lead-portal`| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_DASHBOARD_URL` |

Attach custom domains
`dashboard-staging.<your-domain>` and `portal-staging.<your-domain>`
(or use Vercel preview URLs if DNS isn't ready — just keep the
`NEXT_PUBLIC_*` vars in sync with whatever URL is live).

---

## 4. Provider webhook configuration

Every webhook below is HMAC-gated by an env secret. Paste each into the
provider's dashboard AND into Railway env; verify round-trip with
the sanity curl at the bottom.

| Provider         | URL (replace `<api>`) | Env var                        |
| ---------------- | --------------------- | ------------------------------ |
| Resend outbound  | `https://<api>/v1/webhooks/resend` | `RESEND_WEBHOOK_SECRET`     |
| Resend inbound   | `https://<api>/v1/webhooks/email-inbound?secret=<x>` | `RESEND_INBOUND_SECRET` + MX record on your reply domain |
| 360dialog        | `https://<api>/v1/webhooks/whatsapp?tenant_id=<uuid>` | `DIALOG360_WEBHOOK_SECRET`  |
| Pixart           | `https://<api>/v1/webhooks/pixart` | `PIXART_WEBHOOK_SECRET`        |
| NeverBounce      | — (API pull only)     | `NEVERBOUNCE_API_KEY`          |

**Sanity**: unauthenticated calls must return 401. If any return 200,
the signature verification is not live — stop, investigate:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://<api>/v1/webhooks/resend
# 401 ✓
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://<api>/v1/webhooks/pixart
# 401 ✓  (in dev mode returns 200 — in staging it must be 401)
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://<api>/v1/webhooks/whatsapp
# 401 or 400 ✓
```

---

## 5. Test tenant seed

Two options; option A is the fastest path.

### Option A — onboarding wizard (~5 min)

1. Navigate to `https://dashboard-staging.<domain>/onboarding` in a
   fresh incognito window.
2. Complete all steps (company "Solare Napoli Test", brand colors,
   territory CAP 80100 Napoli, admin user).
3. The wizard does **not** collect NeverBounce / 360dialog / Resend
   inbound secrets. Run this one-off SQL in the Supabase SQL editor:
   ```sql
   UPDATE tenants SET settings = settings || jsonb_build_object(
     'neverbounce_api_key',   'nb_…',
     'dialog360_token',       'dialog_…',
     'resend_webhook_secret', 'rs_…'
   ) WHERE slug = 'solare-napoli-test';
   ```

### Option B — seed script

```bash
cd apps/api
.venv/bin/python scripts/seed_test_tenant.py --tenant-slug solare-napoli-test
```

Idempotent; use `--reset` to wipe leads/campaigns/events before
re-seeding. See `apps/api/scripts/seed_test_tenant.py`.

---

## 6. Smoke test

Run `docs/SMOKE_TEST.md` end-to-end. If all 15 steps pass on a fresh
tenant in one continuous run, staging is ready.

---

## Exit criteria

1. **Code** — `Settings` validator is live; 365+ Python tests + 28+ TS
   tests are green in CI.
2. **Secrets** — `grep -r REPLACE_ME .env.staging` returns nothing on
   the deployed env; the API starts without raising `ValueError`.
3. **DB** — 28 migrations applied; publication check shows `events`,
   `lead_replies`, `conversations`.
4. **Webhooks** — all 4 sanity curls return 401.
5. **Smoke** — `docs/SMOKE_TEST.md` passes end-to-end.

When all five check, share `https://dashboard-staging.<domain>` with
your first beta installer.
