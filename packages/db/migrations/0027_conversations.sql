-- Migration 0027 — WhatsApp conversations (Part B.8)
--
-- Stores the full bidirectional conversation thread between a lead and the
-- AI conversational agent. Each row is one conversation session (one phone
-- number per lead). Messages are stored as a JSONB array for simplicity:
-- no need for a separate messages table given the low cardinality.
--
-- State machine:
--   active   → AI replies automatically (up to AUTO_REPLY_LIMIT turns)
--   handoff  → AI detects "talk to human" intent or hits turn limit;
--              operator takes over, AI stops replying
--   closed   → Conversation ended (appointment booked or operator closed)

CREATE TABLE conversations (
  id                   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id              UUID         NOT NULL REFERENCES leads(id)   ON DELETE CASCADE,

  -- Transport
  channel              TEXT         NOT NULL DEFAULT 'whatsapp'
                         CHECK (channel IN ('whatsapp', 'sms')),
  whatsapp_phone       TEXT         NOT NULL,    -- lead's E.164 phone, ex: "393331234567"
  last_inbound_id      TEXT,                     -- 360dialog wamid of last received msg

  -- State
  state                TEXT         NOT NULL DEFAULT 'active'
                         CHECK (state IN ('active', 'handoff', 'closed')),

  -- Full thread as JSONB array of {role, content, ts, id?}
  -- role = "lead" | "ai" | "system"
  messages             JSONB        NOT NULL DEFAULT '[]'::jsonb,

  -- Counters (denormalised for quick dashboard queries)
  turn_count           INT          NOT NULL DEFAULT 0,   -- total messages (both directions)
  auto_replies_count   INT          NOT NULL DEFAULT 0,   -- AI-generated replies sent

  last_message_at      TIMESTAMPTZ,
  created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Unique per (tenant, phone) — one conversation thread per number
CREATE UNIQUE INDEX idx_conversations_tenant_phone
  ON conversations (tenant_id, whatsapp_phone);

CREATE INDEX idx_conversations_lead
  ON conversations (lead_id);

CREATE INDEX idx_conversations_tenant_recent
  ON conversations (tenant_id, last_message_at DESC NULLS LAST);

CREATE INDEX idx_conversations_state
  ON conversations (tenant_id, state)
  WHERE state = 'active';

-- updated_at trigger (reuse the same helper used on other tables)
CREATE OR REPLACE FUNCTION update_conversations_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_conversations_updated_at
  BEFORE UPDATE ON conversations
  FOR EACH ROW EXECUTE FUNCTION update_conversations_updated_at();

-- RLS: tenants can only read their own conversations via Supabase client.
-- Writes always go through the service-role key (ConversationAgent).
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "conversations_tenant_select"
  ON conversations FOR SELECT
  USING (tenant_id = auth_tenant_id());

-- Realtime publication: so the lead detail page can receive live
-- conversation updates without polling (same pattern as events table).
ALTER PUBLICATION supabase_realtime ADD TABLE public.conversations;
