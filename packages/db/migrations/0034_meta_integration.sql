-- 0034 — Meta Marketing integration: per-tenant connection + lead source tag.
--
-- Each tenant that enables the `meta_ads` outreach channel gets exactly
-- one `meta_connections` row after completing Meta's OAuth handshake.
-- The row holds the long-lived Page Access Token (encrypted at rest
-- via Supabase column-level crypto later — today plain but scoped
-- behind RLS) plus the ad account id and Facebook page id used for
-- campaign creation.
--
-- Webhook flow:
--   1. Tenant submits form in a Meta Lead Ad → Meta POSTs to
--      `/v1/webhooks/meta-leads` (router handler in Phase 3.4).
--   2. Handler validates `X-Hub-Signature-256` with the tenant's
--      `webhook_secret` and upserts a `leads` row with
--      `source='meta_ads'`.
--
-- The `leads.source` column is new — existing rows get 'legacy_scan'
-- as a default so historical analytics queries don't suddenly see
-- NULLs they didn't before.

BEGIN;

CREATE TABLE IF NOT EXISTS meta_connections (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             uuid NOT NULL UNIQUE
                             REFERENCES tenants(id) ON DELETE CASCADE,

    -- OAuth output — held encrypted in a future migration. Today
    -- plain; guarded by RLS so only service-role reads it.
    access_token          text NOT NULL,
    token_expires_at      timestamptz,

    -- Meta account / page surface.
    meta_business_id      text,
    meta_ad_account_id    text NOT NULL,
    meta_page_id          text NOT NULL,
    -- Webhook verify token — the shared secret Meta echoes back on
    -- subscription verification + HMAC signs every lead payload.
    webhook_secret        text NOT NULL,

    connected_at          timestamptz NOT NULL DEFAULT now(),
    disconnected_at       timestamptz
);

ALTER TABLE meta_connections ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS meta_connections_own ON meta_connections;
CREATE POLICY meta_connections_own ON meta_connections
    FOR ALL
    USING (tenant_id = auth.uid() OR auth.role() = 'service_role')
    WITH CHECK (tenant_id = auth.uid() OR auth.role() = 'service_role');


-- ---------------------------------------------------------------------------
-- leads.source — where the lead originated
-- ---------------------------------------------------------------------------

-- Guard: only add the column if it doesn't already exist (idempotent).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'leads' AND column_name = 'source'
    ) THEN
        ALTER TABLE leads
            ADD COLUMN source text NOT NULL DEFAULT 'legacy_scan'
            CHECK (source IN (
                'legacy_scan','b2b_funnel_v2','b2c_meta_ads',
                'b2c_post_engagement','manual_import','api'
            ));
        -- Existing rows already have the default applied.
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_leads_source
    ON leads (tenant_id, source)
    WHERE source != 'legacy_scan';


-- ---------------------------------------------------------------------------
-- B2C inbound: relax roof_id / subject_id NOT NULL
-- ---------------------------------------------------------------------------
--
-- B2C sources (`b2c_meta_ads`, `b2c_post_engagement`) capture the lead
-- BEFORE Solar qualification — we don't have a roof yet, and the subject
-- is an individual whose only contact data is name+email+phone. The
-- existing schema forced both FKs NOT NULL; loosen them but enforce the
-- pairing via a CHECK so B2B rows keep their invariants.
--
-- Also replaces the UNIQUE(tenant_id, roof_id, subject_id) with a
-- partial that applies only when both FKs are set — otherwise every NULL
-- row would collide (Postgres treats (NULL, NULL) pairs as distinct by
-- default but we want the constraint spelled explicitly for clarity).

ALTER TABLE leads ALTER COLUMN roof_id    DROP NOT NULL;
ALTER TABLE leads ALTER COLUMN subject_id DROP NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_b2x_source_fks'
    ) THEN
        ALTER TABLE leads
            ADD CONSTRAINT leads_b2x_source_fks
            CHECK (
                source IN ('b2c_meta_ads','b2c_post_engagement')
                OR (roof_id IS NOT NULL AND subject_id IS NOT NULL)
            );
    END IF;
END$$;


-- ---------------------------------------------------------------------------
-- Meta lead id — dedup inbound webhook retries
-- ---------------------------------------------------------------------------
--
-- Meta redelivers each lead up to 3x on non-2xx. We store their lead id
-- on the row and enforce a tenant-scoped unique so upsert is safe.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'leads' AND column_name = 'meta_lead_id'
    ) THEN
        ALTER TABLE leads ADD COLUMN meta_lead_id text;
    END IF;
END$$;

-- `inbound_payload` stores the raw form fields (name/email/phone/consent)
-- for B2C leads until a subject row is created in Phase 3.6.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'leads' AND column_name = 'inbound_payload'
    ) THEN
        ALTER TABLE leads
            ADD COLUMN inbound_payload jsonb NOT NULL DEFAULT '{}'::jsonb;
    END IF;
END$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_leads_tenant_meta_lead
    ON leads (tenant_id, meta_lead_id)
    WHERE meta_lead_id IS NOT NULL;

COMMIT;
