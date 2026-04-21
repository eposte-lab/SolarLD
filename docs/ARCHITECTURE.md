# Architecture — SolarLead

## 1. System Overview

SolarLead is a **multi-tenant SaaS** that automates lead generation for Italian solar installers via a **7-phase agentic pipeline**:

```
Hunter → Identity → Scoring → Creative → Outreach → Tracking → Compliance
```

Each agent is an **idempotent async module**: same input → same output (or no-op). Communication between agents happens through the **database state machine** (status fields on `roofs`/`leads`), not direct function calls. Background work runs on **arq (Redis-backed)**.

## 2. Layers

| Layer | Tech | Location |
|---|---|---|
| UI (installer) | Next.js 15 App Router | `apps/dashboard/` |
| UI (lead) | Next.js 15 | `apps/lead-portal/` |
| API + Agents | FastAPI (Python 3.12) | `apps/api/` |
| Video rendering | Remotion (Node 20) | `apps/video-renderer/` |
| DB | Supabase (Postgres 16 + Auth + Storage + Realtime) | `packages/db/` |
| Queue / cache | Redis (Upstash in prod) | `infra/docker-compose.yml` |

## 3. Multi-tenancy

All tenant-scoped tables have `tenant_id UUID NOT NULL REFERENCES tenants(id)`. Isolation is enforced by **Postgres Row Level Security**:

```sql
CREATE POLICY leads_all ON leads
  FOR ALL
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());
```

The helper `auth_tenant_id()` reads the caller's JWT (`auth.uid()`) and looks up their tenant binding in `tenant_members`.

**Service role** (used by backend workers) bypasses RLS — this is the expected behavior for cross-tenant system workers like the Hunter Agent cron.

Global reference tables (`ateco_consumption_profiles`, `regional_incentives`, `scoring_weights`, `global_blacklist`) are read-all.

## 4. Pipeline State Machine

```
roof.status:       discovered → identified → scored → rendered → outreach_sent → engaged → converted
                                                                                       ↘ blacklisted
                                                                                       ↘ rejected

lead.pipeline_status: new → sent → delivered → opened → clicked → engaged → whatsapp → appointment → closed_won
                                                                                                 ↘ closed_lost
                                                                                                 ↘ blacklisted
```

Transitions happen via:
- **Agents** mutating their target table at end of `execute()`.
- **Webhooks** updating status from external providers (Resend, Pixartprinting, 360dialog). Stripe is deferred — tier activation is manual for beta.

## 5. Idempotency

Every agent is idempotent. Key design principles:

1. **Deterministic keys**: `roofs.geohash` + `tenant_id` as unique key → re-scanning the same area never duplicates rows.
2. **Event deduplication**: webhook handlers check Redis (24h TTL) for a seen `event_id` before processing.
3. **Status guards**: agents check current `status` before acting → `if status != 'discovered': no-op`.
4. **pii_hash**: subjects are identified by hash of normalized PII, not ephemeral surrogate IDs.

## 6. Compliance / GDPR

- **Global blacklist** is a single source of truth across all tenants, matched by `pii_hash`.
- Before any outreach send, the Compliance Agent verifies `pii_hash NOT IN global_blacklist`.
- Opt-out flows insert into blacklist AND cancel pending `campaigns` AND mark `leads.pipeline_status = 'blacklisted'`.
- Right-to-erasure requests trigger hard-delete of `subjects + leads + campaigns + events` within 30 days.
- PII in logs is scrubbed via Sentry/Better Stack filters.

## 7. Observability

- **Sentry** — error tracking (backend + frontend).
- **Structured logs** — `structlog` JSON in prod, console in dev.
- **arq dashboard** — job inspection (Redis Commander profile in docker-compose).
- **Supabase Realtime** — frontend subscribes to `events` changes scoped by tenant.

## 8. Local Dev Flow

```bash
# 1. Start Redis
pnpm db:start

# 2. Apply migrations to Supabase (remote)
cd packages/db && pnpm migrate

# 3. Run backend
pnpm api:dev

# 4. Run dashboards
pnpm dashboard:dev
pnpm lead-portal:dev
```
