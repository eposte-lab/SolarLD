-- Migration 0042 — tenant_inboxes: multi-inbox email sending
--
-- Enables a single tenant to send outreach from multiple email addresses
-- on the same verified domain. The OutreachAgent's InboxSelector picks the
-- least-recently-used inbox that hasn't hit its daily cap or been paused
-- after a provider error.
--
-- Design decisions:
--   * daily_cap is per-inbox (default 50). Total domain output =
--     sum(active_inboxes.daily_cap), bounded by the domain-level Redis
--     hourly cap in rate_limit_service.py.
--   * paused_until: auto-populated by the service when Resend returns 429
--     or 5xx for a given sender. Prevents one bad inbox from blocking others.
--   * sent_date + total_sent_today: lazy daily reset — the service reads
--     "if sent_date < today treat counter as 0". No cron needed.
--   * inbox_id on campaigns: for per-inbox deliverability analytics and
--     traceback when a bounce/complaint needs to be attributed to a sender.
--
-- Backward compat: tenants with zero rows in tenant_inboxes continue to
-- use the single-inbox path (outreach@email_from_domain) unchanged.

BEGIN;

-- ── tenant_inboxes ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tenant_inboxes (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid        NOT NULL
                            REFERENCES tenants(id) ON DELETE CASCADE,

    -- Sender identity
    email               text        NOT NULL,
    display_name        text        NOT NULL DEFAULT '',
    reply_to_email      text,               -- null → use inbox email as reply-to

    -- Optional per-inbox HTML signature appended *below* the main template.
    -- Keeps personal touches (title, mobile, photo) without touching the
    -- global template.
    signature_html      text,

    -- Rate cap: how many emails this inbox may send per calendar day.
    -- 50/day × 5 inboxes = 250/day — a safe cold-domain volume.
    -- Operator can raise after full warm-up (30 days).
    daily_cap           int         NOT NULL DEFAULT 50
                            CHECK (daily_cap > 0 AND daily_cap <= 2000),

    -- Auto-pause: set to now() + N hours on Resend 429/5xx.
    -- InboxSelector skips inboxes where paused_until > now().
    paused_until        timestamptz,
    pause_reason        text,               -- human-readable, shown in UI

    -- Lazy daily counter — no cron needed.
    -- If sent_date < current_date, the service treats total_sent_today as 0.
    sent_date           date,
    total_sent_today    int         NOT NULL DEFAULT 0
                            CHECK (total_sent_today >= 0),
    last_sent_at        timestamptz,

    active              bool        NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    -- One email address per tenant (dedup guard)
    UNIQUE (tenant_id, email)
);

-- Efficient inbox selection: the service queries active inboxes ordered by
-- last_sent_at ASC (round-robin: pick the least-recently-used first).
CREATE INDEX IF NOT EXISTS tenant_inboxes_select_idx
    ON tenant_inboxes (tenant_id, active, last_sent_at NULLS FIRST)
    WHERE active = true;

-- Support per-inbox deliverability analytics join.
CREATE INDEX IF NOT EXISTS tenant_inboxes_tenant_idx
    ON tenant_inboxes (tenant_id, created_at DESC);

-- ── campaigns.inbox_id FK ────────────────────────────────────────────────────
-- Add sender attribution to every outreach record. SET NULL on inbox delete
-- so historical campaigns rows aren't orphaned.

ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS inbox_id uuid
        REFERENCES tenant_inboxes(id) ON DELETE SET NULL;

-- Speed up "show all sends from inbox X" drilldown in the UI.
CREATE INDEX IF NOT EXISTS campaigns_inbox_id_idx
    ON campaigns (inbox_id, created_at DESC)
    WHERE inbox_id IS NOT NULL;

-- ── RLS ──────────────────────────────────────────────────────────────────────

ALTER TABLE tenant_inboxes ENABLE ROW LEVEL SECURITY;

-- auth_tenant_id() is defined in 0011 — resolves tenant_id from the JWT.
CREATE POLICY tenant_inboxes_tenant_isolation
    ON tenant_inboxes
    FOR ALL
    USING (tenant_id = auth_tenant_id());

-- Dashboard SSR and direct Supabase reads use the authenticated role.
GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_inboxes TO authenticated;

COMMIT;
