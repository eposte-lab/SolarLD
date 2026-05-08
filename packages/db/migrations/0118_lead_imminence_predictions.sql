-- ──────────────────────────────────────────────────────────────────
-- Migration 0118 — Lead Imminence Predictor
-- ──────────────────────────────────────────────────────────────────
-- Daily-recomputed table that ranks engaged leads by their probability
-- of becoming "hot" (interested enough to convert) within the next
-- 48-72h. Populated by ``imminence_predictions_cron`` (06:30 UTC).
--
-- The cron computes 4 deterministic sub-scores per eligible lead:
--   * behavioral (40%) — recent portal activity, video watch, bolletta
--   * temporal   (20%) — engagement acceleration, time-since-last-event
--   * contextual (20%) — sector conv-rate, score, dimensione, kWp
--   * comparative (20%) — similarity to leads that closed in last 90d
-- and stores the weighted combo as ``imminence_score`` (0-100).
--
-- For the top N (score >= 60) per tenant, Haiku generates the
-- ``primary_reasons`` + ``suggested_action`` + ``talking_points``
-- fields so the operator sees a human "perché chiamarlo oggi"
-- explanation instead of a bare number.
--
-- One row per (tenant, lead, prediction_date). The dashboard reads
-- only ``prediction_date = CURRENT_DATE`` rows; older rows are kept
-- 30 days then purged by retention_cron (handled separately).
-- ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS lead_imminence_predictions (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    lead_id                     UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,

    prediction_date             DATE NOT NULL DEFAULT CURRENT_DATE,

    imminence_score             SMALLINT NOT NULL CHECK (imminence_score BETWEEN 0 AND 100),
    behavioral_score            SMALLINT NOT NULL CHECK (behavioral_score BETWEEN 0 AND 100),
    temporal_score              SMALLINT NOT NULL CHECK (temporal_score BETWEEN 0 AND 100),
    contextual_score            SMALLINT NOT NULL CHECK (contextual_score BETWEEN 0 AND 100),
    comparative_score           SMALLINT NOT NULL CHECK (comparative_score BETWEEN 0 AND 100),

    -- Human-readable rationale (Haiku, only set for score >= 60).
    primary_reasons             TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    talking_points              TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],

    suggested_action            TEXT CHECK (
        suggested_action IN ('call_now','call_today','send_followup','wait_24h')
    ),
    suggested_channel           TEXT CHECK (
        suggested_channel IN ('phone','email','whatsapp')
    ),
    best_time_to_contact        TEXT CHECK (
        best_time_to_contact IN ('morning_9_11','afternoon_14_17','now')
    ),

    -- Feedback loop fields (populated by user actions / outcome cron).
    actioned_at                 TIMESTAMPTZ,
    actioned_by_user_id         UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    action_taken                TEXT CHECK (
        action_taken IN ('called','emailed','whatsapped','ignored','marked_invalid')
    ),
    outcome                     TEXT CHECK (
        outcome IN ('became_hot_within_72h','became_hot_later','no_change','lost','not_yet_evaluated')
    ),
    outcome_evaluated_at        TIMESTAMPTZ,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (tenant_id, lead_id, prediction_date)
);

-- Today's list for a tenant — the dashboard's hot path.
CREATE INDEX IF NOT EXISTS idx_imminence_today
    ON lead_imminence_predictions (tenant_id, prediction_date, imminence_score DESC);

-- Outcome accuracy queries (audit dashboard).
CREATE INDEX IF NOT EXISTS idx_imminence_outcome
    ON lead_imminence_predictions (outcome, outcome_evaluated_at)
    WHERE outcome IS NOT NULL;

-- Per-lead history.
CREATE INDEX IF NOT EXISTS idx_imminence_lead
    ON lead_imminence_predictions (lead_id, prediction_date DESC);

-- ──────────────────────────────────────────────────────────────────
-- RLS — tenant isolation, identical pattern to other tenant tables
-- ──────────────────────────────────────────────────────────────────
ALTER TABLE lead_imminence_predictions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS imminence_select_own_tenant ON lead_imminence_predictions;
CREATE POLICY imminence_select_own_tenant ON lead_imminence_predictions
    FOR SELECT
    USING (
        tenant_id IN (
            SELECT tenant_id FROM tenant_members WHERE user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS imminence_update_own_tenant ON lead_imminence_predictions;
CREATE POLICY imminence_update_own_tenant ON lead_imminence_predictions
    FOR UPDATE
    USING (
        tenant_id IN (
            SELECT tenant_id FROM tenant_members WHERE user_id = auth.uid()
        )
    );

-- Service role bypasses RLS — the cron writes via service client.

-- ──────────────────────────────────────────────────────────────────
-- Denormalised mirror on `leads` for fast list-ordering without join
-- ──────────────────────────────────────────────────────────────────
ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS last_imminence_score SMALLINT,
    ADD COLUMN IF NOT EXISTS last_imminence_predicted_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_leads_last_imminence
    ON leads (tenant_id, last_imminence_score DESC NULLS LAST)
    WHERE last_imminence_score IS NOT NULL;
