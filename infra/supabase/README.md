# Supabase — local + remote setup

## First-time setup

```bash
# 1. Install Supabase CLI
brew install supabase/tap/supabase

# 2. Link to the remote project
cd /Users/alfonsogallo/SolarLD
supabase link --project-ref ppabjpryzzkksrbnledy

# 3. (Optional) Start a full local stack
supabase start     # launches Postgres + Auth + Storage + Studio on :54321

# 4. Push migrations to remote
supabase db push
```

## Applying migrations manually (no CLI)

If you prefer to apply the SQL files directly through the Supabase SQL Editor:

1. Open https://supabase.com/dashboard/project/ppabjpryzzkksrbnledy/sql
2. Paste the contents of each file in `packages/db/migrations/` in order (0001 → 0012).
3. Finally run `packages/db/seed/ateco_seed.sql` for initial reference data.

## Generating TypeScript types after schema changes

```bash
cd packages/db
pnpm gen:types
```

This writes `packages/shared-types/src/database.types.ts` containing the exact row/insert/update types per table.
