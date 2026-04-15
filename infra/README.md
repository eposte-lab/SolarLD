# infra/

Local dev stack and infra configuration.

## Quick Start

```bash
# Start Redis (and optionally local Postgres)
docker compose up -d

# Also bring up a local Postgres (instead of using remote Supabase)
docker compose --profile local-db up -d

# Bring up Redis Commander (job inspector at :8081)
docker compose --profile tools up -d
```

## Supabase

See `supabase/README.md` for linking and applying migrations against the remote project `ppabjpryzzkksrbnledy`.

## Deployment

- **Backend (FastAPI + arq workers)** → Railway
  - Service 1: `uvicorn src.main:app` (from `apps/api/Dockerfile`)
  - Service 2: `arq src.workers.main.WorkerSettings`
  - Redis addon: Upstash or Railway Redis
- **Frontend (dashboard + lead-portal)** → Vercel (two projects, root dir `apps/dashboard` and `apps/lead-portal`)
- **Video-renderer** → Railway or a dedicated Remotion Lambda
