# Sprint 0 — Foundation (Delivered)

**Duration**: Weeks 1-2
**Goal**: Standing-up the full monorepo scaffold so Sprint 1 can start building the Hunter Agent immediately.

## What's in place

### Monorepo
- [x] pnpm workspaces + Turborepo
- [x] Prettier + TypeScript base config
- [x] `.env.example` with all services listed
- [x] `.gitignore`, `README.md`, `tsconfig.base.json`
- [x] Root `package.json` with top-level scripts

### Database (`packages/db/`)
- [x] 12 SQL migrations covering:
  - Extensions (`uuid-ossp`, `pgcrypto`, `pg_trgm`, `postgis`) + enum types
  - `tenants` + `tenant_members` + `updated_at` trigger helper
  - `territories`
  - `roofs`
  - `subjects` (with `pii_hash`)
  - `leads` (central entity)
  - `campaigns`
  - `events` (monthly range-partitioned)
  - `global_blacklist`
  - Auxiliary: `ateco_consumption_profiles`, `regional_incentives`, `scoring_weights`, `email_warmup_status`, `api_usage_log`
  - RLS policies with `auth_tenant_id()` helper
  - Storage buckets (`renderings`, `postcards`, `branding`)
- [x] ATECO seed data

### Backend (`apps/api/`)
- [x] FastAPI app with routers: auth, tenants, territories, leads, campaigns, events, webhooks, public, admin, health
- [x] Core infra: config (pydantic-settings), structlog, async SQLAlchemy, Redis, Supabase clients (service + user), JWT security
- [x] Pydantic models + enums (mirror Postgres enums)
- [x] Agent skeletons: Hunter, Identity, Scoring, Creative, Outreach, Tracking, Compliance
- [x] `AgentBase` with `run()` / audit-event emission / idempotency pattern
- [x] `ComplianceAgent` has a working blacklist propagation implementation
- [x] arq worker definition (`workers/main.py`)
- [x] Smoke tests (health, OpenAPI, compliance hash determinism)
- [x] Multi-stage `Dockerfile` for Railway

### Frontend — Dashboard (`apps/dashboard/`)
- [x] Next.js 15 App Router with route groups `(auth)` / `(dashboard)`
- [x] TailwindCSS + HSL CSS variables
- [x] Supabase SSR client (browser + server + middleware)
- [x] Auth middleware with redirect rules
- [x] Pages: `/`, `/login`, `/` (overview), `/leads`, `/leads/[id]`, `/territories`, `/campaigns`, `/analytics`, `/settings`
- [x] API client with auto-JWT injection
- [x] Format helpers (eur/kwh/kwp)

### Frontend — Lead Portal (`apps/lead-portal/`)
- [x] Next.js 15 separate app (security isolation — no Supabase client)
- [x] `/lead/[slug]` ISR page with rendering + ROI + WhatsApp CTA
- [x] `noindex` headers + not-found page

### Video Renderer (`apps/video-renderer/`)
- [x] Remotion composition for before/after transition (180 frames, 30fps)
- [x] Express sidecar server with `/render` endpoint
- [x] Schema-validated input (zod)

### Shared Packages
- [x] `@solarlead/shared-types` — TS domain types (enums, Tenant, Territory, Lead)
- [x] `@solarlead/ui` — shadcn-style Button, Card, Badge primitives

### Infra
- [x] `docker-compose.yml` (Redis + optional local Postgres + tools profile)
- [x] `supabase/config.toml` for local stack
- [x] Deployment docs (Railway + Vercel)

### CI/CD
- [x] GitHub Actions `ci.yml`:
  - API: ruff lint + format + mypy + pytest (with Postgres + Redis services)
  - Frontend: pnpm install + turbo typecheck/lint/build
  - SQL: applies all migrations against a fresh Postgres to validate syntax
- [x] `deploy.yml` (Railway hook placeholder)
- [x] Dependabot for pip + npm + actions
- [x] PR template

## What's NOT in place (by design — future sprints)

- Hunter Agent real implementation (Sprint 1-2)
- Identity Agent real implementation (Sprint 1-2)
- Scoring Agent algorithm (Sprint 3)
- Creative Agent Mapbox + Replicate + Remotion pipeline (Sprint 4-5)
- Email outreach with Resend + Mailwarm (Sprint 6-7)
- Postal outreach with Pixartprinting (Sprint 8)
- WhatsApp integration (Sprint 9)
- Compliance Agent full opt-out flows (Sprint 10)
- Onboarding wizard (Sprint 11)
- Launch hardening (Sprint 12)

## Handoff checklist for Sprint 1

Before kicking off Sprint 1 (Hunter Agent):

- [ ] Obtain Google Solar API key → set `GOOGLE_SOLAR_API_KEY`
- [ ] Obtain Mapbox token → set `MAPBOX_ACCESS_TOKEN`
- [ ] Run `supabase link --project-ref ppabjpryzzkksrbnledy`
- [ ] Run `supabase db push` to apply all migrations
- [ ] Create first test tenant row via SQL editor
- [ ] Create Supabase Auth user and bind it to the test tenant via `tenant_members`
- [ ] Generate `SUPABASE_SERVICE_ROLE_KEY` from the dashboard and add to `.env`
- [ ] Run `pnpm install && pip install -e "apps/api[dev]"`
- [ ] Verify all apps boot (`pnpm dev`)
- [ ] Verify CI passes on the first commit
