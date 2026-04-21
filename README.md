# SolarLead

**Agentic Lead Generation Platform for Solar Installers** — Multi-tenant SaaS that automates lead discovery, qualification, and first contact for Italian photovoltaic installers through a 7-phase pipeline orchestrated by Claude agents.

**Status**: Sprint 0 — Foundation (in progress)
**Version**: 0.1.0
**Owner**: Alfonso Gallo

---

## Architecture

```
┌────────────────────────────────────────────────────────┐
│              CLIENT LAYER (Next.js)                    │
│  Dashboard Installer  │  Public Lead Portal (slug)     │
└────────────────────────────────────────────────────────┘
                          ↓ ↑
┌────────────────────────────────────────────────────────┐
│              API LAYER (FastAPI)                       │
│  /auth  /tenants  /territories  /leads  /campaigns     │
│  /events  /webhooks  /public                           │
└────────────────────────────────────────────────────────┘
                          ↓ ↑
┌────────────────────────────────────────────────────────┐
│         AGENT LAYER (Claude + BullMQ)                  │
│  Hunter → Identity → Scoring → Creative →              │
│  Outreach → Tracking → Compliance                      │
└────────────────────────────────────────────────────────┘
                          ↓ ↑
┌────────────────────────────────────────────────────────┐
│      DATA LAYER (Supabase — Postgres + Storage)        │
│  tenants │ territories │ roofs │ subjects │ leads      │
│  campaigns │ events │ global_blacklist │ …             │
└────────────────────────────────────────────────────────┘
```

See the full [PRD](./SolarLead_PRD_v1.docx) for specification details.

---

## Repository Structure

```
solarlead/
├── apps/
│   ├── api/              FastAPI backend (Python 3.12) — agents + REST API
│   ├── dashboard/        Next.js 15 installer dashboard (App Router)
│   ├── lead-portal/      Next.js 15 public lead portal (slug-based)
│   └── video-renderer/   Remotion Node sidecar — video generation
├── packages/
│   ├── db/               Supabase schema + SQL migrations
│   ├── shared-types/     Shared TypeScript types (TS from Postgres)
│   └── ui/               shadcn/ui component library
├── infra/
│   ├── docker-compose.yml   Local dev stack (Redis + optional Postgres)
│   └── supabase/            Supabase local config
├── .github/workflows/       CI/CD (GitHub Actions)
└── docs/                    Technical docs
```

---

## Tech Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12, FastAPI, uvicorn, BullMQ, Redis |
| Frontend | Next.js 15 (App Router), TypeScript, TailwindCSS, shadcn/ui |
| Database | Supabase (Postgres 16 + Auth + Storage + Realtime) |
| AI | Anthropic Claude Sonnet 4.5, Replicate (Stable Diffusion + ControlNet) |
| Video | Remotion + FFmpeg |
| Email | Resend |
| Postal | Pixartprinting Direct Mail API |
| WhatsApp | 360dialog |
| Payments | Manual for beta (Stripe wiring deferred) |
| Hosting | Railway (backend), Vercel (frontend), Supabase (DB) |
| Monitoring | Sentry, PostHog (EU), Better Stack |

---

## Quick Start (Local Dev)

### Prerequisites
- Node.js ≥ 20.11
- pnpm ≥ 10
- Python ≥ 3.12
- Docker Desktop

### Setup

```bash
# 1. Install JS dependencies
pnpm install

# 2. Setup Python env for API
cd apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cd ../..

# 3. Copy env file
cp .env.example .env
# → Edit .env with real values

# 4. Start local infra (Redis)
pnpm db:start

# 5. Run all apps
pnpm dev
```

### URLs
- Dashboard: http://localhost:3000
- Lead Portal: http://localhost:3001
- API: http://localhost:8000
- API docs: http://localhost:8000/docs

---

## Development Workflow

```bash
# Run a single app
pnpm api:dev
pnpm dashboard:dev
pnpm lead-portal:dev

# Linting / typecheck / tests
pnpm lint
pnpm typecheck
pnpm test

# Format code
pnpm format
```

---

## Deployment

- **Backend** → Railway (Python + Redis addon)
- **Dashboard** → Vercel (Next.js)
- **Lead Portal** → Vercel (separate project for security isolation)
- **Database** → Supabase Pro (project ID: `ppabjpryzzkksrbnledy`)

See [`docs/deployment.md`](./docs/deployment.md) for details.

---

## License

UNLICENSED — Private property of Alfonso Gallo.
