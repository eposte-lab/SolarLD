-- ============================================================
-- 0009 — global_blacklist
-- ============================================================
-- GDPR-compliant global opt-out table: no RLS, readable by all
-- tenants/service workers, writable only by compliance agent.

CREATE TABLE IF NOT EXISTS global_blacklist (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  pii_hash    TEXT NOT NULL UNIQUE,
  reason      blacklist_reason NOT NULL,
  source      TEXT,                -- which flow triggered it
  notes       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_blacklist_hash     ON global_blacklist(pii_hash);
CREATE INDEX idx_blacklist_reason   ON global_blacklist(reason);
CREATE INDEX idx_blacklist_created  ON global_blacklist(created_at DESC);
