-- ============================================================
-- 0074 — Device authorization gate for demo accounts
-- ============================================================
--
-- Purpose
--   Limit the number of physical devices that can authenticate
--   into a demo tenant. Each tenant has a fixed slot for an
--   admin device (the operator's machine, manually authorised)
--   plus N-1 dynamic client slots that auto-fill on first
--   login and stick.
--
-- Threat model
--   Demo credentials get shared (Slack, screenshots, etc.).
--   Without this gate a single login → infinite seats. With it,
--   the 4th device that tries to log in is hard-blocked at
--   middleware level and the operator gets visibility on every
--   device that has ever held a session.
--
-- Fingerprint strategy (implemented in lib/auth/device-gate.ts)
--   Primary: a server-issued opaque cookie token (HttpOnly,
--   Secure, SameSite=Lax, 1 year). Hard-bound to the row id.
--   Fallback when cookie is cleared: SHA256(user_agent || '|' ||
--   ip_subnet24). Lossy, but enough to recognise the same
--   browser on the same network within the same NAT.
--
-- The combination wins reasonable fights without imposing
-- a full canvas-fingerprint script (which ad-blockers and
-- privacy extensions routinely break).
--
-- Schema
--   tenants gains 3 knobs:
--     demo_device_limit_enabled    — master toggle.
--     demo_device_max_total        — hard cap (default 3).
--     demo_device_idle_timeout_minutes — auto-logout window.
--
--   tenant_authorized_devices stores one row per
--   (tenant, fingerprint). Soft-revocable via revoked_at.
--
-- RLS
--   Tenant isolation strictly via auth_tenant_id().

BEGIN;

-- ── 1. Tenant config knobs ───────────────────────────────────────────────

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS demo_device_limit_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS demo_device_max_total INT NOT NULL DEFAULT 3
        CHECK (demo_device_max_total BETWEEN 1 AND 20),
    ADD COLUMN IF NOT EXISTS demo_device_idle_timeout_minutes INT NOT NULL DEFAULT 30
        CHECK (demo_device_idle_timeout_minutes BETWEEN 5 AND 1440);

COMMENT ON COLUMN tenants.demo_device_limit_enabled IS
    'Master toggle for the device-authorization gate. When TRUE the '
    'Next.js middleware refuses login from the (max_total+1)-th device.';

COMMENT ON COLUMN tenants.demo_device_max_total IS
    'Total seats including the admin device. Default 3 = 1 admin + 2 clients.';

COMMENT ON COLUMN tenants.demo_device_idle_timeout_minutes IS
    'Client-side idle-logout window in minutes. The dashboard clears '
    'the session after this much inactivity (mouse/keyboard).';

-- ── 2. Authorized devices table ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tenant_authorized_devices (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    -- Soft fingerprint = SHA256(ua || '|' || ip_subnet_24).
    -- Used as fallback identification when the cookie is cleared.
    fingerprint_hash TEXT NOT NULL,

    -- Server-issued opaque token sent back as HttpOnly cookie.
    -- Random 32-byte hex; must be globally unique.
    cookie_token    TEXT NOT NULL,

    -- Role decides whether the row counts against the dynamic
    -- client quota. Admin devices are pinned manually and never
    -- expire / never count.
    role            TEXT NOT NULL DEFAULT 'client'
        CHECK (role IN ('admin', 'client')),

    -- Human-friendly label derived from UA. May be edited by
    -- the operator from /settings/devices.
    display_name    TEXT,
    user_agent      TEXT,
    ip_subnet       TEXT,

    authorized_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ,

    -- Last user that authenticated through this device.
    last_user_id    UUID REFERENCES auth.users(id) ON DELETE SET NULL,

    -- Cookie token must be globally unique (active OR revoked).
    -- Active fingerprints must be unique per tenant.
    CONSTRAINT tad_cookie_unique UNIQUE (cookie_token)
);

-- Active fingerprints unique per tenant.
CREATE UNIQUE INDEX IF NOT EXISTS idx_tad_active_fingerprint
    ON tenant_authorized_devices (tenant_id, fingerprint_hash)
    WHERE revoked_at IS NULL;

-- Hot path: middleware lookup by cookie_token.
CREATE INDEX IF NOT EXISTS idx_tad_cookie_active
    ON tenant_authorized_devices (cookie_token)
    WHERE revoked_at IS NULL;

-- Admin dashboard listing.
CREATE INDEX IF NOT EXISTS idx_tad_tenant_listing
    ON tenant_authorized_devices (tenant_id, authorized_at DESC);

-- ── 3. RLS ───────────────────────────────────────────────────────────────

ALTER TABLE tenant_authorized_devices ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tad_tenant_iso ON tenant_authorized_devices;
CREATE POLICY tad_tenant_iso ON tenant_authorized_devices
    FOR ALL
    USING (tenant_id = auth_tenant_id() OR auth.role() = 'service_role')
    WITH CHECK (tenant_id = auth_tenant_id() OR auth.role() = 'service_role');

GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_authorized_devices TO authenticated;

COMMIT;
