# @solarlead/db

Supabase schema, migrations, and seed data.

## Layout

```
migrations/
├── 0001_extensions_and_enums.sql     # pgcrypto, uuid, postgis, enums
├── 0002_tenants.sql                  # tenants + tenant_members + updated_at trigger
├── 0003_territories.sql              # geographic coverage per tenant
├── 0004_roofs.sql                    # discovered buildings
├── 0005_subjects.sql                 # B2B/B2C owners + pii_hash
├── 0006_leads.sql                    # central entity
├── 0007_campaigns.sql                # outreach sends
├── 0008_events.sql                   # partitioned audit trail
├── 0009_global_blacklist.sql         # GDPR opt-out
├── 0010_auxiliary_tables.sql         # ATECO, incentives, scoring weights, warmup
├── 0011_rls_policies.sql             # multi-tenant isolation
└── 0012_storage_buckets.sql          # Supabase storage + RLS
```

## Usage

### Push to remote Supabase
```bash
supabase link --project-ref ppabjpryzzkksrbnledy
supabase db push
```

### Run locally (docker)
```bash
supabase start
supabase db reset --local
```

### Generate TypeScript types
```bash
pnpm gen:types
```

## Multi-tenancy

All tenant-scoped tables use RLS. The helper `auth_tenant_id()` resolves the current user's `tenant_id` via `tenant_members`. Service role connections bypass RLS — used by backend agents/workers.

## Events partitioning

`events` is range-partitioned monthly by `occurred_at`. Use `ensure_events_partition(date)` to create missing months; wire to cron in production.
