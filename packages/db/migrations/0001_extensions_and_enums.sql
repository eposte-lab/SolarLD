-- ============================================================
-- 0001 — Extensions & Enums
-- ============================================================
-- Core Postgres extensions + custom enum types for the domain.

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "postgis";

-- ============================================================
-- Enum types
-- ============================================================

-- tenant tier
CREATE TYPE tenant_tier AS ENUM ('founding', 'pro', 'enterprise');

-- tenant status
CREATE TYPE tenant_status AS ENUM ('onboarding', 'active', 'paused', 'churned');

-- territory type
CREATE TYPE territory_type AS ENUM ('cap', 'comune', 'provincia', 'regione');

-- roof data source
CREATE TYPE roof_data_source AS ENUM ('google_solar', 'mapbox_ai_fallback');

-- subject classification
CREATE TYPE subject_type AS ENUM ('b2b', 'b2c', 'unknown');

-- roof pipeline status
CREATE TYPE roof_status AS ENUM (
  'discovered',
  'identified',
  'scored',
  'rendered',
  'outreach_sent',
  'engaged',
  'converted',
  'blacklisted',
  'rejected'
);

-- lead score tier
CREATE TYPE lead_score_tier AS ENUM ('hot', 'warm', 'cold', 'rejected');

-- lead pipeline status
CREATE TYPE lead_status AS ENUM (
  'new',
  'sent',
  'delivered',
  'opened',
  'clicked',
  'engaged',
  'whatsapp',
  'appointment',
  'closed_won',
  'closed_lost',
  'blacklisted'
);

-- outreach channel
CREATE TYPE outreach_channel AS ENUM ('email', 'postal');

-- installer feedback
CREATE TYPE installer_feedback AS ENUM (
  'qualified',
  'not_interested',
  'not_reachable',
  'contract_signed',
  'wrong_data'
);

-- campaign status
CREATE TYPE campaign_status AS ENUM ('pending', 'sent', 'delivered', 'failed', 'cancelled');

-- blacklist reason
CREATE TYPE blacklist_reason AS ENUM (
  'user_optout',
  'manual',
  'regulatory',
  'bounce_hard',
  'complaint'
);
