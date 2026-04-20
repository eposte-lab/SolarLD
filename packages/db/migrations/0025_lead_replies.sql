-- ============================================================
-- Migration 0025 — lead_replies: stores replies from leads to
-- outreach emails, enriched by RepliesAgent (Claude).
-- ============================================================

create table if not exists public.lead_replies (
  id               uuid        primary key default gen_random_uuid(),
  tenant_id        uuid        not null references public.tenants(id) on delete cascade,
  lead_id          uuid        not null references public.leads(id) on delete cascade,

  -- raw inbound email fields
  from_email       text        not null,
  reply_subject    text,
  body_text        text,
  received_at      timestamptz not null default now(),

  -- Claude analysis output (null until RepliesAgent processes the row)
  sentiment        text        check (sentiment in ('positive','neutral','negative','unclear')),
  intent           text        check (intent in ('interested','question','objection','appointment_request','unsubscribe','other')),
  urgency          text        check (urgency in ('high','medium','low')),
  suggested_reply  text,
  analysis_error   text,
  analyzed_at      timestamptz,

  created_at       timestamptz not null default now()
);

-- Fast look-ups: all replies for a lead (detail page), all unread for a tenant
create index if not exists lead_replies_lead_id_idx
  on public.lead_replies (lead_id, received_at desc);

create index if not exists lead_replies_tenant_created_idx
  on public.lead_replies (tenant_id, created_at desc);

-- RLS: tenants may only read their own rows.
-- INSERT/UPDATE is done by the API service role (bypasses RLS).
alter table public.lead_replies enable row level security;

create policy "lead_replies_tenant_select"
  on public.lead_replies
  for select
  using (tenant_id = auth_tenant_id());

-- No INSERT/UPDATE/DELETE policy for authenticated role — only service-role writes.
