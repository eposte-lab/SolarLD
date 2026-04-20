/**
 * Database row shapes as returned by Supabase from the dashboard.
 *
 * These mirror the SQL schema (see `packages/db/migrations/`). They're
 * narrower than the Python models: we only track the columns the
 * dashboard actually renders. Keep in sync with the migration files.
 */

export type LeadScoreTier = 'hot' | 'warm' | 'cold' | 'rejected';

export type TerritoryType = 'cap' | 'comune' | 'provincia' | 'regione';

/** Commercial tier as defined by the `tenant_tier` enum (migration 0001). */
export type TenantTier = 'founding' | 'pro' | 'enterprise';

/**
 * Shape of `tenants.settings` JSONB the dashboard cares about. We
 * only annotate the keys we actually read — anything else is kept
 * in a pass-through index signature so we don't lose admin-only
 * config when round-tripping.
 */
export interface TenantSettings {
  feature_flags?: Record<string, boolean>;
  [key: string]: unknown;
}

/** Bounding box stored as JSONB on territories.bbox. */
export interface TerritoryBbox {
  ne: { lat: number; lng: number };
  sw: { lat: number; lng: number };
}

/** Territory row — one geographic coverage slot per tenant. */
export interface TerritoryRow {
  id: string;
  tenant_id: string;
  type: TerritoryType;
  code: string;
  name: string;
  bbox: TerritoryBbox | null;
  excluded: boolean;
  priority: number;
  created_at: string;
  updated_at: string;
}

export type LeadStatus =
  | 'new'
  | 'sent'
  | 'delivered'
  | 'opened'
  | 'clicked'
  | 'engaged'
  | 'whatsapp'
  | 'appointment'
  | 'closed_won'
  | 'closed_lost'
  | 'blacklisted';

export type OutreachChannel = 'email' | 'postal';
export type SubjectType = 'b2b' | 'b2c' | 'unknown';
export type CampaignStatus = 'pending' | 'sent' | 'delivered' | 'failed' | 'cancelled';

export interface RoiData {
  annual_savings_eur?: number;
  payback_years?: number;
  co2_saved_kg?: number;
  estimated_kwp?: number;
}

export interface TenantRow {
  id: string;
  business_name: string;
  brand_primary_color: string | null;
  brand_logo_url: string | null;
  contact_email: string;
  whatsapp_number: string | null;
  email_from_domain: string | null;
  email_from_name: string | null;
  email_from_domain_verified_at?: string | null;
  tier: TenantTier;
  settings: TenantSettings;
}

/**
 * Snapshot row from ``domain_reputation`` — one per
 * (tenant, email_from_domain, date). The dashboard reads the most
 * recent row for the current tenant's domain and renders the card +
 * alarm banner in /settings. Rates are precomputed server-side.
 */
export interface DomainReputationRow {
  id: string;
  tenant_id: string;
  email_from_domain: string;
  as_of_date: string; // YYYY-MM-DD
  sent_count: number;
  delivered_count: number;
  bounced_count: number;
  complained_count: number;
  opened_count: number;
  delivery_rate: number | null;
  bounce_rate: number | null;
  complaint_rate: number | null;
  open_rate: number | null;
  alarm_bounce: boolean;
  alarm_complaint: boolean;
  created_at: string;
}

export interface RoofSummary {
  address: string | null;
  comune: string | null;
  provincia: string | null;
  cap: string | null;
  estimated_kwp: number | null;
  estimated_yearly_kwh: number | null;
  area_sqm: number | null;
}

export interface SubjectSummary {
  type: SubjectType;
  business_name: string | null;
  owner_first_name: string | null;
  owner_last_name: string | null;
  decision_maker_email: string | null;
  decision_maker_email_verified: boolean;
}

/** Lead row as displayed in the list view (joined with subject + roof summary). */
export interface LeadListRow {
  id: string;
  public_slug: string;
  pipeline_status: LeadStatus;
  score: number;
  score_tier: LeadScoreTier;
  outreach_channel: OutreachChannel | null;
  outreach_sent_at: string | null;
  outreach_opened_at: string | null;
  dashboard_visited_at: string | null;
  created_at: string;
  subjects: SubjectSummary | null;
  roofs: RoofSummary | null;
  // Engagement rollup columns (migration 0021, refreshed nightly by
  // engagement_rollup_cron). Default to 0 in the DB, so these are
  // never null for rows written after the migration.
  engagement_score: number;
  engagement_score_updated_at: string | null;
  portal_sessions: number;
  portal_total_time_sec: number;
  deepest_scroll_pct: number;
}

/** Full detail row — same as list + renderings + roi_data. */
export interface LeadDetailRow extends LeadListRow {
  rendering_image_url: string | null;
  rendering_video_url: string | null;
  rendering_gif_url: string | null;
  roi_data: RoiData;
  outreach_delivered_at: string | null;
  outreach_clicked_at: string | null;
  whatsapp_initiated_at: string | null;
  feedback: string | null;
  feedback_notes: string | null;
  score_breakdown: Record<string, number>;
}

/**
 * Campaign row — mirrors migration 0007 exactly.
 *
 * Important: per-recipient engagement (delivered / opened / clicked)
 * is NOT stored on this table. The Resend/tracking webhooks update
 * the parent **lead** (`leads.outreach_*_at`). The campaign only
 * tracks its own send-side lifecycle via the `status` enum plus
 * `sent_at`. To render engagement in a campaigns table we join the
 * lead — see `CampaignWithLeadEngagement` below.
 */
export interface CampaignRow {
  id: string;
  lead_id: string;
  tenant_id: string;
  channel: OutreachChannel;
  sequence_step: number;
  status: CampaignStatus;
  template_id: string;
  email_subject: string | null;
  email_message_id: string | null;
  email_html_url: string | null;
  postal_provider_order_id: string | null;
  postal_tracking_number: string | null;
  postal_pdf_url: string | null;
  scheduled_for: string;
  sent_at: string | null;
  cost_cents: number;
  failure_reason: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * Campaign row joined with the parent lead's outreach timestamps so
 * each campaign row in the list view can show whether the recipient
 * engaged at any point. Engagement is recorded at the lead level —
 * not per-step — so this collapses to a lead-wide signal.
 */
export interface CampaignWithLeadEngagement extends CampaignRow {
  leads: {
    outreach_delivered_at: string | null;
    outreach_opened_at: string | null;
    outreach_clicked_at: string | null;
  } | null;
}

export interface EventRow {
  /** BIGSERIAL — Supabase serializes it as number; keep both shapes tolerated. */
  id: number | string;
  tenant_id: string | null;
  lead_id: string | null;
  event_type: string;
  event_source: string | null;
  payload: Record<string, unknown> | null;
  /** Partition key on `events`. There is no `created_at` column. */
  occurred_at: string;
}

/** Aggregated KPIs for the overview page. */
export interface OverviewKpis {
  leads_sent_30d: number;
  hot_leads: number;
  appointments_30d: number;
  closed_won_30d: number;
}

// ---------------------------------------------------------------------------
// Tenant operational config (Sprint 9 — scan_mode wizard)
// ---------------------------------------------------------------------------

export type ScanMode = 'b2b_precision' | 'opportunistic' | 'volume';
export type TargetSegment = 'b2b' | 'b2c';

/** Per-segment thresholds applied after a Google Solar scan. */
export interface TechnicalFilters {
  min_area_sqm: number;
  min_kwp: number;
  max_shading: number;
  min_exposure_score: number;
}

/**
 * Mirror of the Python `TenantConfig` dataclass — one row per tenant.
 *
 * Drives Hunter's dispatcher (scan_mode) and the onboarding wizard
 * redirect guard (wizard_completed_at null = send to /onboarding).
 */
export interface TenantConfigRow {
  tenant_id: string;
  scan_mode: ScanMode;
  target_segments: TargetSegment[];

  // Google Places discovery (scan_mode='b2b_precision')
  place_type_whitelist: string[];
  place_type_priority: Record<string, number>;

  // ATECO (Tier 2 metadata)
  ateco_whitelist: string[];
  ateco_blacklist: string[];
  ateco_priority: Record<string, number>;

  // Size filters (meaningful only post-Atoka)
  min_employees: number | null;
  max_employees: number | null;
  min_revenue_eur: number | null;
  max_revenue_eur: number | null;

  // Per-segment technical thresholds
  technical_filters: {
    b2b?: Partial<TechnicalFilters>;
    b2c?: Partial<TechnicalFilters>;
  };

  // Scoring
  scoring_threshold: number;
  scoring_weights: Record<string, Record<string, number>>;

  // Budgets
  monthly_scan_budget_eur: number;
  monthly_outreach_budget_eur: number;

  // Scan strategy
  scan_priority_zones: string[];
  scan_grid_density_m: number;

  // Enrichment Tier 2
  atoka_enabled: boolean;
  atoka_monthly_cap_eur: number;

  // Wizard
  wizard_completed_at: string | null;
}

/** One row of `ateco_google_types` — wizard dropdown option. */
export interface AtecoOption {
  ateco_code: string;
  ateco_label: string;
  wizard_group: string;
  google_types: string[];
  priority_hint: number;
  target_segment: TargetSegment;
}

// ---------------------------------------------------------------------------
// Audit log (migration 0024, Part B.11)
// ---------------------------------------------------------------------------

/**
 * One row from ``audit_log`` — immutable trail of operator mutations.
 * ``id`` is BIGSERIAL so we type it as number | string (Supabase may
 * return large integers as strings depending on the client version).
 */
export interface AuditLogRow {
  id: number | string;
  tenant_id: string;
  actor_user_id: string | null;
  action: string;
  target_table: string | null;
  target_id: string | null;
  diff: Record<string, unknown> | null;
  at: string;
}

// ---------------------------------------------------------------------------
// Conversion attribution (migration 0023, Part B.6)
// ---------------------------------------------------------------------------

/**
 * Funnel stage for closed-loop attribution.
 * Kept in lockstep with the CHECK constraint in migration 0023 and
 * ``_upsert_conversion`` in ``apps/api/src/routes/public.py``.
 */
export type ConversionStage = 'booked' | 'quoted' | 'won' | 'lost';

/** One row from ``conversions`` — one per (lead, stage). */
export interface ConversionRow {
  id: string;
  tenant_id: string;
  lead_id: string;
  stage: ConversionStage;
  /** Deal value in euro-cents. Null when recorded via pixel only. */
  amount_cents: number | null;
  /** 'pixel' | 'api' | 'manual' */
  source: string;
  closed_at: string;
  created_at: string;
}

/** Aggregated stats returned by ``getConversionStats`` — used by the overview card. */
export interface ConversionStats {
  booked: number;
  quoted: number;
  won: number;
  lost: number;
  /** Sum of ``amount_cents`` for stage='won' rows in the window (in cents). */
  won_value_cents: number;
}

// ---------------------------------------------------------------------------
// CRM outbound webhooks (migration 0017, Part B.7 integration)
// ---------------------------------------------------------------------------

/**
 * Event types the dispatcher supports. Kept in lockstep with
 * ``SUPPORTED_EVENTS`` in ``apps/api/src/services/crm_webhook_service.py``.
 * If you add a new event server-side, extend this union so the
 * dashboard's picker offers it.
 */
export type CrmWebhookEvent =
  | 'lead.created'
  | 'lead.scored'
  | 'lead.outreach_sent'
  | 'lead.engaged'
  | 'lead.contract_signed';

/** Subscription row as returned by ``GET /v1/crm-webhooks`` (secret masked). */
export interface CrmWebhookRow {
  id: string;
  label: string;
  url: string;
  events: CrmWebhookEvent[];
  active: boolean;
  last_status: string | null;
  last_delivered_at: string | null;
  failure_count: number;
  created_at: string;
  updated_at: string;
}

/** Delivery attempt row — last N shown in the deliveries panel. */
export interface CrmWebhookDeliveryRow {
  id: number;
  event_type: CrmWebhookEvent;
  attempt: number;
  status_code: number | null;
  error: string | null;
  occurred_at: string;
}

// ---------------------------------------------------------------------------
// Template A/B experiments (migration 0026, Part B.4)
// ---------------------------------------------------------------------------

/** One row from ``template_experiments``. */
export interface ExperimentRow {
  id: string;
  tenant_id: string;
  name: string;
  variant_a_subject: string;
  variant_b_subject: string;
  split_pct: number;
  started_at: string;
  ended_at: string | null;
  winner: 'a' | 'b' | null;
  winner_declared_at: string | null;
  created_at: string;
}

export interface ExperimentVariantStats {
  sends: number;
  opens: number;
  clicks: number;
  open_rate: number;
  click_rate: number;
}

export type ExperimentVerdict = 'a_wins' | 'b_wins' | 'in_corso' | 'no_data';

/** Shape returned by ``GET /v1/experiments/{id}/stats``. */
export interface ExperimentStats {
  experiment_id: string;
  a: ExperimentVariantStats;
  b: ExperimentVariantStats;
  prob_a_wins_open: number;
  prob_a_wins_click: number;
  verdict_open: ExperimentVerdict;
  verdict_click: ExperimentVerdict;
  min_sample_met: boolean;
}

// ---------------------------------------------------------------------------
// WhatsApp Conversations (migration 0027, Part B.8)
// ---------------------------------------------------------------------------

export type ConversationState = 'active' | 'handoff' | 'closed';
export type ConversationChannel = 'whatsapp' | 'sms';
export type ConversationMessageRole = 'lead' | 'ai' | 'system';

/** One message in the conversations.messages JSONB array. */
export interface ConversationMessage {
  role: ConversationMessageRole;
  content: string;
  ts: string;           // ISO timestamp
  id?: string;          // 360dialog wamid (inbound only)
  handoff_message?: boolean;
}

/** One row from the `conversations` table. */
export interface ConversationRow {
  id: string;
  tenant_id: string;
  lead_id: string;
  channel: ConversationChannel;
  whatsapp_phone: string;
  last_inbound_id: string | null;
  state: ConversationState;
  messages: ConversationMessage[];
  turn_count: number;
  auto_replies_count: number;
  last_message_at: string | null;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Branding & AI variants (Part B.13)
// ---------------------------------------------------------------------------

export interface DnsRecord {
  type: string;
  name: string;
  value: string;
  priority: number | null;
  ttl: number | null;
  status: string;
}

export interface DomainStatusResponse {
  domain_id: string;
  domain: string;
  status: string;        // not_started | pending | verified | failed
  dns_records: DnsRecord[];
  created_at: string | null;
}

export interface AiEmailVariant {
  subject: string;
  preheader: string;
  body_preview: string;
  rationale: string;
}

export interface GenerateVariantsResponse {
  variants: AiEmailVariant[];
  subject_type: string;
  tone: string;
}

// ---------------------------------------------------------------------------
// Lead replies (migration 0025, Part B.2)
// ---------------------------------------------------------------------------

export type ReplySentiment = 'positive' | 'neutral' | 'negative' | 'unclear';
export type ReplyIntent =
  | 'interested'
  | 'question'
  | 'objection'
  | 'appointment_request'
  | 'unsubscribe'
  | 'other';
export type ReplyUrgency = 'high' | 'medium' | 'low';

/**
 * One row from ``lead_replies``.
 * Claude analysis columns are null until RepliesAgent has processed the row.
 */
export interface LeadReplyRow {
  id: string;
  tenant_id: string;
  lead_id: string;
  from_email: string;
  reply_subject: string | null;
  body_text: string | null;
  received_at: string;
  /** Null until analysed by RepliesAgent. */
  sentiment: ReplySentiment | null;
  intent: ReplyIntent | null;
  urgency: ReplyUrgency | null;
  suggested_reply: string | null;
  analysis_error: string | null;
  analyzed_at: string | null;
  created_at: string;
}
