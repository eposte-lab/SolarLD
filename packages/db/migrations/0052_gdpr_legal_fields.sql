-- Migration 0052 — GDPR legal fields + domain reputation enforcement
--
-- Sprint 6.5.
--
-- Part A: Legal fields on tenants (required for mandatory GDPR footer
--   in outreach emails). If legal_name / vat_number / legal_address are
--   missing, the OutreachAgent blocks sends with reason='gdpr_footer_missing'.
--
-- Part B: legal_basis on subjects (pii_hashes) — tracks the lawful
--   basis under which we hold each contact's data. Defaults to
--   'legitimate_interest_b2b' (art. 6.1.f GDPR) for all prospected B2B
--   leads. If a future B2C or consent-based channel is added, the basis
--   changes to 'consent'.
--
-- Part C: Auto-enforcement columns on tenant_email_domains — the nightly
--   reputation digest already writes alarm_bounce / alarm_complaint to
--   domain_reputation; we now add fast-path columns on the domain so the
--   enforcement service can pause a domain with a single UPDATE without
--   joining through domain_reputation.

BEGIN;

-- ── Part A: tenants legal fields ───────────────────────────────────────────

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS legal_name    text,   -- Ragione sociale (es. "Agenda Pro SRL")
    ADD COLUMN IF NOT EXISTS vat_number    text,   -- P.IVA (es. "IT12345678901")
    ADD COLUMN IF NOT EXISTS legal_address text;   -- Indirizzo sede legale

COMMENT ON COLUMN tenants.legal_name IS
    'Legal entity name used in the mandatory GDPR email footer. '
    'Outreach is blocked until set.';

COMMENT ON COLUMN tenants.vat_number IS
    'P.IVA / VAT number. Required for GDPR footer under IT/EU law.';

COMMENT ON COLUMN tenants.legal_address IS
    'Registered office address for GDPR footer (brief form is fine, '
    'e.g. "Via Roma 1, 80100 Napoli").';


-- ── Part B: legal_basis on subjects (pii_hashes) ───────────────────────────

ALTER TABLE subjects
    ADD COLUMN IF NOT EXISTS legal_basis text
        NOT NULL DEFAULT 'legitimate_interest_b2b'
        CHECK (legal_basis IN (
            'legitimate_interest_b2b',  -- Art. 6.1.f GDPR — default for B2B funnel
            'consent',                  -- Explicit opt-in (future B2C)
            'contract'                  -- Pre-contractual / existing customer
        ));

COMMENT ON COLUMN subjects.legal_basis IS
    'GDPR lawful basis for holding this contact record. '
    'legitimate_interest_b2b is the default for all outreach-generated leads.';


-- ── Part C: reputation enforcement on tenant_email_domains ─────────────────
-- These columns mirror what domain_reputation already computes nightly, but
-- live directly on the domain row so the enforcement service can check them
-- in O(1) without a JOIN.

ALTER TABLE tenant_email_domains
    ADD COLUMN IF NOT EXISTS alarm_bounce     bool NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS alarm_complaint  bool NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS last_enforcement_at timestamptz,
    ADD COLUMN IF NOT EXISTS enforcement_reason  text;

COMMENT ON COLUMN tenant_email_domains.alarm_bounce IS
    'Set true by reputation_enforcement_service when bounce_rate > 5%. '
    'Causes domain auto-pause for 48h.';

COMMENT ON COLUMN tenant_email_domains.alarm_complaint IS
    'Set true when complaint_rate > 0.3% (Gmail threshold). '
    'Causes domain auto-pause for 48h.';


-- ── Notifications table: ensure it exists for enforcement alerts ────────────
-- (It may not exist in older schemas; we ADD column only if table exists.)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'notifications'
    ) THEN
        ALTER TABLE notifications
            ADD COLUMN IF NOT EXISTS domain_id uuid
                REFERENCES tenant_email_domains(id) ON DELETE SET NULL;
    END IF;
END $$;

COMMIT;
