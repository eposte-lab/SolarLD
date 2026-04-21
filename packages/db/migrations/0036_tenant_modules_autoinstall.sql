-- 0036 — Auto-install tenant_modules for every new tenant.
--
-- Context: migration 0032 backfilled five module rows for every tenant
-- that existed **at the moment 0032 ran**. Tenants created *after*
-- that migration (e.g. signups during staging bring-up) get nothing:
-- the application code that creates a tenant never inserted the five
-- rows, so `GET /v1/modules` returned an empty list and the frontend
-- had to synthesise defaults on the fly. That made the invariant
--
--     "every tenant has exactly five tenant_modules rows"
--
-- a best-effort claim instead of a DB-enforced truth, and meant every
-- reader had to defensively handle the "row missing" case. This
-- migration promotes it to a real invariant.
--
-- How:
--   1. A trigger `AFTER INSERT ON tenants` inserts five rows with
--      `config = '{}'::jsonb` and `version = 0`. The **API read path**
--      (tenant_module_service) hydrates `{}` through the Pydantic
--      schemas on every read, so the wire format always contains the
--      full default config — defaults live in exactly one place
--      (Pydantic), not duplicated into SQL here.
--   2. `version = 0` marks the row as "auto-installed, never touched
--      by the installer". The existing `tenant_modules_touch` trigger
--      bumps `version` to 1 on the first user-initiated UPDATE, which
--      is what `wizard_complete` will key off (see service refactor).
--   3. A one-off backfill at the bottom fills in the **missing** rows
--      for tenants that were created between migration 0032 and this
--      one — e.g. the test accounts created during the v2 staging
--      bring-up today. Uses `ON CONFLICT DO NOTHING` so it's safe to
--      re-run and never clobbers a row the installer has edited.
--
-- Why not write the full default JSON in SQL here (like 0032 did):
-- keeping the defaults in Pydantic + re-hydrating on every read means
-- we can add a field to a module's schema without a data migration —
-- old rows with `{}` or partial JSON automatically gain the new field
-- at read time. The SQL-backfilled rows from 0032 would drift without
-- the hydration step; after this refactor they don't.

BEGIN;

-- ---------------------------------------------------------------------------
-- Trigger — install the 5 module rows on tenant INSERT.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION install_default_tenant_modules() RETURNS trigger AS $$
BEGIN
    INSERT INTO tenant_modules (tenant_id, module_key, config, active, version)
    VALUES
        (NEW.id, 'sorgente',  '{}'::jsonb, true, 0),
        (NEW.id, 'tecnico',   '{}'::jsonb, true, 0),
        (NEW.id, 'economico', '{}'::jsonb, true, 0),
        (NEW.id, 'outreach',  '{}'::jsonb, true, 0),
        (NEW.id, 'crm',       '{}'::jsonb, true, 0)
    ON CONFLICT (tenant_id, module_key) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tenants_install_modules ON tenants;
CREATE TRIGGER trg_tenants_install_modules
    AFTER INSERT ON tenants
    FOR EACH ROW EXECUTE FUNCTION install_default_tenant_modules();

-- ---------------------------------------------------------------------------
-- One-off backfill — plug any orphan tenant created between 0032 and now.
-- ---------------------------------------------------------------------------

INSERT INTO tenant_modules (tenant_id, module_key, config, active, version)
SELECT t.id, mk, '{}'::jsonb, true, 0
FROM tenants t
CROSS JOIN (
    VALUES ('sorgente'),('tecnico'),('economico'),('outreach'),('crm')
) AS m(mk)
ON CONFLICT (tenant_id, module_key) DO NOTHING;

COMMIT;
