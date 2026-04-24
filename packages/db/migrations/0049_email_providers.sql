-- Migration 0049 — email providers: Gmail/M365 OAuth + provider abstraction
--
-- Sprint 6.1 foundation. Until now every tenant_inbox was sent via Resend
-- (implicit). For cold B2B outreach we need Gmail/Workspace inboxes via
-- OAuth2 refresh tokens — each inbox is its own reputation unit and
-- Gmail-to-Gmail delivery bypasses most shared-IP reputation problems.
--
-- Backward compat: existing rows keep provider='resend' (column default),
-- the OutreachAgent dispatches via registry so nothing changes for them.
--
-- Security:
--   * Refresh tokens are encrypted at rest with Fernet (symmetric) using
--     APP_SECRET_KEY. The DB never sees the plaintext.
--   * Access tokens are short-lived (~1h) and live in memory mostly; we
--     cache the last one here so parallel workers can reuse it without
--     hammering the refresh endpoint.
--   * oauth_token_expires_at lets us refresh proactively (5 min buffer)
--     instead of reactively on 401.

BEGIN;

ALTER TABLE tenant_inboxes
    ADD COLUMN IF NOT EXISTS provider text NOT NULL DEFAULT 'resend'
        CHECK (provider IN ('resend','gmail_oauth','m365_oauth','smtp')),
    ADD COLUMN IF NOT EXISTS oauth_refresh_token_encrypted text,
    ADD COLUMN IF NOT EXISTS oauth_access_token_encrypted text,
    ADD COLUMN IF NOT EXISTS oauth_token_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS oauth_scope text,
    ADD COLUMN IF NOT EXISTS oauth_account_email text,  -- ex: alfonso@agendasolar.it
    ADD COLUMN IF NOT EXISTS oauth_connected_at timestamptz,
    ADD COLUMN IF NOT EXISTS oauth_last_error text,
    ADD COLUMN IF NOT EXISTS oauth_last_error_at timestamptz;

-- Index for the token-refresh cron: find all OAuth inboxes whose access
-- token is about to expire. Partial index keeps it tiny.
CREATE INDEX IF NOT EXISTS tenant_inboxes_oauth_expiry_idx
    ON tenant_inboxes (oauth_token_expires_at)
    WHERE provider IN ('gmail_oauth','m365_oauth');

-- Fast lookup for "has this tenant connected any Gmail inbox?" in the UI.
CREATE INDEX IF NOT EXISTS tenant_inboxes_provider_idx
    ON tenant_inboxes (tenant_id, provider)
    WHERE provider != 'resend';

COMMENT ON COLUMN tenant_inboxes.provider IS
    'Send transport. resend=default shared IPs (transactional), '
    'gmail_oauth=Google Workspace per-inbox OAuth (cold outreach), '
    'm365_oauth=Microsoft 365 Graph API, smtp=reserved for future.';

COMMENT ON COLUMN tenant_inboxes.oauth_refresh_token_encrypted IS
    'Fernet-encrypted refresh token. Never log or expose — use '
    'encryption_service.decrypt() only inside the provider.';

COMMIT;
