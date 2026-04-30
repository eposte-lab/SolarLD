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
  /** Dedicated FROM address for follow-up emails. Falls back to outreach@{email_from_domain}. */
  followup_from_email?: string | null;
  tier: TenantTier;
  settings: TenantSettings;
  /**
   * Set once the installer confirms their territorial exclusivity at
   * the end of onboarding. When not null, the dashboard hides the
   * territory add / delete affordances and disables editing of the
   * geographic fields in the sorgente module. Cleared only via ops
   * endpoint `POST /v1/admin/tenants/:id/territory-unlock`.
   */
  territory_locked_at?: string | null;
  territory_locked_by?: string | null;
  /**
   * Device-authorization gate (migration 0074). When enabled, only
   * fingerprints listed in `tenant_authorized_devices` (up to
   * `demo_device_max_total`) can sign in; the rest are routed to
   * /access-denied. Used to cap concurrent demo accounts.
   */
  demo_device_limit_enabled?: boolean;
  demo_device_max_total?: number;
  demo_device_idle_timeout_minutes?: number;
  /**
   * When true, the dashboard hides the Settings hub and any other
   * surfaces that should not be exposed to demo viewers. Set on
   * showroom / sales-demo accounts only.
   */
  is_demo?: boolean;
  /**
   * Lifetime budget for the customer-facing "Avvia test pipeline"
   * banner on `/leads`. Decremented atomically by
   * `POST /v1/demo/test-pipeline` and never resets — the cap protects
   * Solar/Mapbox/Atoka quotas during a sales call. Default 0
   * (feature off) for non-demo tenants. See migration 0077.
   */
  demo_pipeline_test_remaining?: number;
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
  // Solar API data — only loaded on the lead detail page (DETAIL_COLUMNS).
  // Used by the "Dati Solar API" inspection panel to verify the customer
  // quote before sending. `null` when Solar API was never called or skipped.
  exposure?: string | null;
  pitch_degrees?: number | null;
  shading_score?: number | null;
  has_existing_pv?: boolean | null;
  lat?: number | null;
  lng?: number | null;
  status?: string | null;
  raw_data?: Record<string, unknown> | null;
}

export interface SubjectSummary {
  type: SubjectType;
  business_name: string | null;
  owner_first_name: string | null;
  owner_last_name: string | null;
  decision_maker_email: string | null;
  decision_maker_email_verified: boolean;
  /**
   * Decision-maker phone (E.164-ish "+39…" form). NULL when neither Atoka
   * nor the website scraper could find one — the lead is still actionable
   * via email; phone is a credibility nice-to-have for the anagrafica.
   * Free to acquire (Atoka bundles it; scraper is regex over the contact
   * page).
   */
  decision_maker_phone?: string | null;
  /**
   * Provenance for the badge in the anagrafica panel:
   *   'atoka'          — high confidence, paid B2B registry bundle
   *   'website_scrape' — extracted from the company's own contact page
   *   'manual'         — typed in by an operator via the admin seed flow
   * NULL when `decision_maker_phone` is also NULL.
   */
  decision_maker_phone_source?:
    | "atoka"
    | "website_scrape"
    | "manual"
    | null;
  /**
   * Decision-maker role (CEO / Direttore / Sales Manager / …). Free
   * text from Atoka or operator input. Used in the anagrafica panel
   * directly under the name and as a personalisation token in the
   * outreach copy.
   */
  decision_maker_role?: string | null;
  /**
   * ATECO classification fields. `ateco_code` is the dotted code
   * ("49.41"); `ateco_description` is the human-readable italian
   * gloss ("Trasporto di merci su strada"). The dashboard displays
   * the description right next to the code so the operator doesn't
   * have to look up what 49.41 means.
   */
  ateco_code?: string | null;
  ateco_description?: string | null;
  /**
   * Yearly revenue in eurocents (B2B only — populated from Atoka's
   * revenue band). Stored as cents to keep currency math integer-clean.
   * NULL for B2C subjects and for B2B without a published bilancio.
   */
  yearly_revenue_cents?: number | null;
  /** Headcount band, B2B only. NULL when unknown. */
  employees?: number | null;
  /** Company LinkedIn URL — surfaced as a clickable chip. */
  linkedin_url?: string | null;
  /**
   * Operating-site (sede operativa) coordinates. Distinct from the
   * legal HQ on `roofs` because the chamber-of-commerce filing often
   * points to a notary's address, not the actual building. Populated
   * by the cascade in `operating_site_resolver` (Atoka locations[]
   * → website scrape → Google Places → Mapbox HQ centroid).
   */
  sede_operativa_address?: string | null;
  sede_operativa_cap?: string | null;
  sede_operativa_city?: string | null;
  sede_operativa_province?: string | null;
  sede_operativa_lat?: number | null;
  sede_operativa_lng?: number | null;
  /**
   * Provenance badge for the rendering panel:
   *   'atoka'          — Atoka locations[] entry (highest confidence)
   *   'website_scrape' — schema.org/<address>/regex on the website
   *   'google_places'  — Google Places API text search
   *   'mapbox_hq'      — fallback: forward-geocoded HQ (low confidence)
   *   'manual'         — operator override
   * NULL when the cascade has not yet been run for this subject.
   */
  sede_operativa_source?:
    | "atoka"
    | "website_scrape"
    | "google_places"
    | "mapbox_hq"
    | "manual"
    | null;
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
  // Stamped by bump_engagement_score (migration 0066) every time the
  // public portal track endpoint fires. Used by the "Caldi adesso"
  // surface to filter to leads that are currently moving.
  last_portal_event_at: string | null;
  portal_sessions: number;
  portal_total_time_sec: number;
  deepest_scroll_pct: number;
}

/** Full detail row — same as list + renderings + roi_data. */
export interface LeadDetailRow extends LeadListRow {
  rendering_image_url: string | null;
  rendering_video_url: string | null;
  rendering_gif_url: string | null;
  portal_video_slug: string | null;
  roi_data: RoiData;
  outreach_delivered_at: string | null;
  outreach_clicked_at: string | null;
  whatsapp_initiated_at: string | null;
  feedback: string | null;
  feedback_notes: string | null;
  score_breakdown: Record<string, number>;
}

/**
 * Outreach send row — mirrors `outreach_sends` table (migration 0043,
 * previously named `campaigns`).
 *
 * Each row is one individual email / postal / WA message sent to one lead.
 * Per-recipient engagement (delivered / opened / clicked) is NOT stored here;
 * it lives on the parent lead's `outreach_*_at` columns. To render engagement
 * alongside a send, join the lead — see `OutreachSendWithEngagement` below.
 */
export interface OutreachSendRow {
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
  acquisition_campaign_id: string | null;
  inbox_id: string | null;
  created_at: string;
  updated_at: string;
}

/** Backward-compat alias — components that still use CampaignRow compile unchanged. */
export type CampaignRow = OutreachSendRow;

/**
 * Outreach send joined with the parent lead's engagement timestamps.
 * Engagement is lead-level, not per-send, so this is a lead-wide signal.
 */
export interface OutreachSendWithEngagement extends OutreachSendRow {
  leads: {
    outreach_delivered_at: string | null;
    outreach_opened_at: string | null;
    outreach_clicked_at: string | null;
  } | null;
}

/** Backward-compat alias. */
export type CampaignWithLeadEngagement = OutreachSendWithEngagement;

// ---------------------------------------------------------------------------
// Acquisition campaigns (migration 0044)
// ---------------------------------------------------------------------------

export type AcquisitionCampaignStatus = 'draft' | 'active' | 'paused' | 'archived';

/**
 * One row from `acquisition_campaigns` — the strategic targeting entity
 * that bundles the 5 wizard module configs into a named campaign.
 */
export interface AcquisitionCampaignRow {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  is_default: boolean;
  status: AcquisitionCampaignStatus;
  sorgente_config: Record<string, unknown>;
  tecnico_config: Record<string, unknown>;
  economico_config: Record<string, unknown>;
  outreach_config: Record<string, unknown>;
  crm_config: Record<string, unknown>;
  inbox_ids: string[] | null;
  schedule_cron: string | null;
  budget_cap_cents: number | null;
  /**
   * Optional manual copy override (migration 0073). When `enabled=true`
   * the OutreachAgent uses these 4 fields and bypasses the cluster A/B
   * engine. NULL = use cluster A/B (default behaviour).
   */
  custom_copy_override: CampaignCustomCopyOverride | null;
  created_at: string;
  updated_at: string;
}

/** Shape of `acquisition_campaigns.custom_copy_override` JSONB. */
export interface CampaignCustomCopyOverride {
  enabled: boolean;
  copy_subject?: string;
  copy_opening_line?: string;
  copy_proposition_line?: string;
  cta_primary_label?: string;
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

// ---------------------------------------------------------------------------
// Campaign overrides — Sprint 3
// ---------------------------------------------------------------------------

export type CampaignOverrideType = 'mail' | 'geo_subset' | 'ab_test' | 'all';

/**
 * One row from `campaign_overrides`.
 *
 * An override is a time-boxed JSONB patch applied on top of an acquisition
 * campaign's base config during the [start_at, end_at] window (UTC).
 */
export interface CampaignOverrideRow {
  id: string;
  campaign_id: string;
  tenant_id: string;
  label: string;
  override_type: CampaignOverrideType;
  start_at: string;
  end_at: string;
  patch: Record<string, unknown>;
  experiment_id: string | null;
  created_at: string;
  created_by: string | null;
}

/**
 * One row from `lead_quotes` (migration 0081).
 *
 * A formal preventivo (PDF) generated from a hot lead. Versions are
 * immutable: each "Salva e genera PDF" creates a new row with
 * `version = max+1`; the previous row's `status` flips to `superseded`.
 *
 * `auto_fields` is the snapshot of system-computed values at issue
 * time (tenant/azienda/solar/econ/render); `manual_fields` is what the
 * installer typed in the editor (commerciale, tech, prezzo, pagamento,
 * tempi, note). Both are JSONB on the Postgres side.
 */
export type LeadQuoteStatus = 'draft' | 'issued' | 'superseded';

export interface LeadQuoteRow {
  id: string;
  tenant_id: string;
  lead_id: string;
  preventivo_number: string; // e.g. "2026/PV/0042"
  preventivo_seq: number;
  version: number;
  status: LeadQuoteStatus;
  auto_fields: Record<string, unknown>;
  manual_fields: Record<string, unknown>;
  pdf_url: string | null;
  hero_url: string | null;
  created_at: string;
  updated_at: string;
}
