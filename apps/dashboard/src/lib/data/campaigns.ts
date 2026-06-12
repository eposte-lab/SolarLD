/**
 * Outreach sends + events data access — server-side, RLS-scoped.
 *
 * Schema note (migration 0043): the `campaigns` table was renamed to
 * `outreach_sends`. This file now reads from `outreach_sends`.
 * Each row is one individual message send (email / postal / WA).
 *
 * Open and click signals live on the parent **lead** under
 * `outreach_opened_at` / `outreach_clicked_at`, updated by the
 * Resend webhook in TrackingAgent. That means "open/click rate" at
 * the outreach_sends table level is really a lead-wide signal — we
 * compute it as the fraction of unique leads (with at least one send)
 * that ever engaged.
 *
 * Acquisition campaigns (strategic targeting entities) live in a
 * separate table: `acquisition_campaigns` (see data/acquisition-campaigns.ts).
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import type {
  CampaignRow,
  CampaignWithLeadEngagement,
  EventRow,
} from '@/types/db';

/** True when the current tenant is under super-admin trial moderation. */
async function isModeratedTenant(): Promise<boolean> {
  const ctx = await getCurrentTenantContext();
  return ctx?.is_moderated ?? false;
}

/**
 * Freeze a joined lead's prospect REACTIONS (email opens/clicks) for a
 * moderated tenant when the operator hasn't promoted the contatto yet
 * (`operator_released_at IS NULL`). Delivery stays visible — it's a
 * mail-server signal, not a prospect reaction. Mutates in place.
 *
 * This mirrors the scheda freeze (lib/data/moderation-freeze.ts) so the
 * "Invii" section + send detail can't leak engagement Total Trade isn't
 * supposed to see until the operator releases the contatto.
 */
function freezeLeadEngagement(
  lead:
    | {
        operator_released_at?: string | null;
        outreach_opened_at?: string | null;
        outreach_clicked_at?: string | null;
      }
    | null
    | undefined,
): void {
  if (!lead || lead.operator_released_at) return;
  lead.outreach_opened_at = null;
  lead.outreach_clicked_at = null;
}

export interface CampaignDeliveryStats {
  total: number;
  delivered: number;
  opened: number;
  clicked: number;
  failed: number;
  delivery_rate: number; // 0-1  delivered / total
  open_rate: number;     // 0-1  opened / delivered  (lead-level)
  click_rate: number;    // 0-1  clicked / delivered (lead-level)
}

/** Concrete columns we read — keeps the select tight + aligned with types. */
const CAMPAIGN_COLUMNS = `
  id, lead_id, tenant_id, channel, sequence_step, status,
  template_id, email_subject, email_message_id, email_html_url,
  postal_provider_order_id, postal_tracking_number, postal_pdf_url,
  scheduled_for, sent_at, cost_cents, failure_reason,
  created_at, updated_at
`.trim();

const CAMPAIGN_WITH_LEAD_COLUMNS = `
  ${CAMPAIGN_COLUMNS},
  leads:leads(
    operator_released_at,
    outreach_delivered_at, outreach_opened_at, outreach_clicked_at,
    subjects:subjects(
      business_name, decision_maker_name,
      decision_maker_phone, decision_maker_email
    )
  )
`.trim();

/**
 * Paginated list of campaigns (most recent first), joined with the
 * parent lead's engagement timestamps so the table can render an
 * "opened / clicked" badge per row without a second round-trip.
 */
export async function listCampaigns(
  limit = 50,
): Promise<CampaignWithLeadEngagement[]> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('outreach_sends')
    .select(CAMPAIGN_WITH_LEAD_COLUMNS)
    .order('created_at', { ascending: false })
    .limit(limit);
  if (error) throw new Error(`listCampaigns: ${error.message}`);
  let rows = (data ?? []) as unknown as CampaignWithLeadEngagement[];
  // Moderation freeze: an un-promoted contatto's send view stays frozen at the
  // FIRST outreach. So (1) drop follow-up sends (sequence_step > 1) entirely
  // for contatti the operator hasn't promoted, and (2) hide opens/clicks on
  // the surviving first-touch row (delivery stays visible).
  if (await isModeratedTenant()) {
    rows = rows.filter((row) => {
      const step = (row as { sequence_step?: number | null }).sequence_step ?? 1;
      const released = (row.leads as { operator_released_at?: string | null } | null)
        ?.operator_released_at;
      return !(step > 1 && !released);
    });
    for (const row of rows) freezeLeadEngagement(row.leads);
  }
  return rows;
}

/**
 * Aggregate funnel stats across all campaigns for the current tenant.
 *
 * - ``delivered`` / ``failed`` are campaign-level (read from
 *   ``campaigns.status``).
 * - ``opened`` / ``clicked`` are lead-level: we count distinct
 *   leads (that appear in campaigns) with a non-null
 *   ``outreach_opened_at`` / ``outreach_clicked_at``. This is
 *   consistent with how analytics_funnel in migration 0016 counts
 *   the same stages.
 */
export async function getCampaignDeliveryStats(): Promise<CampaignDeliveryStats> {
  const supabase = await createSupabaseServerClient();

  const [campaignsRes, engagementRes, moderated] = await Promise.all([
    supabase
      .from('outreach_sends')
      .select('status, lead_id, sequence_step, leads:leads(operator_released_at)'),
    // RLS scopes `leads` to the current tenant → we only see our rows.
    supabase
      .from('leads')
      .select('id, operator_released_at, outreach_opened_at, outreach_clicked_at')
      .or('outreach_opened_at.not.is.null,outreach_clicked_at.not.is.null'),
    isModeratedTenant(),
  ]);

  if (campaignsRes.error) {
    throw new Error(`getCampaignDeliveryStats: ${campaignsRes.error.message}`);
  }
  if (engagementRes.error) {
    throw new Error(`getCampaignDeliveryStats: ${engagementRes.error.message}`);
  }

  let campaigns = campaignsRes.data ?? [];
  const engagedLeads = engagementRes.data ?? [];

  // Moderation freeze: an un-promoted contatto's view stays frozen at the first
  // outreach, so follow-up sends (sequence_step > 1) don't count toward the
  // tenant's "invii" total until the operator promotes the contatto.
  if (moderated) {
    campaigns = campaigns.filter((c) => {
      const step = (c as { sequence_step?: number | null }).sequence_step ?? 1;
      const leadRel = (c as { leads?: { operator_released_at?: string | null } | { operator_released_at?: string | null }[] | null }).leads;
      const lead = Array.isArray(leadRel) ? leadRel[0] : leadRel;
      return !(step > 1 && !lead?.operator_released_at);
    });
  }

  const total = campaigns.length;
  // `delivered` here means "successfully handed off to the email provider".
  // This system writes a send as status='sent' and would only upgrade it to
  // 'delivered' if a provider delivery webhook fired — which is NOT wired
  // (opens/clicks are tracked portal-side via route.public, not Resend). A
  // strict status==='delivered' filter was therefore ALWAYS 0, which zeroed
  // every rate (open_rate = opened/delivered = opened/0 = 0) and made the whole
  // KPI strip read "no data". Count 'sent' as delivered so the rates are real.
  const delivered = campaigns.filter(
    (c) => c.status === 'sent' || c.status === 'delivered',
  ).length;
  const failed = campaigns.filter((c) => c.status === 'failed').length;

  // Restrict the lead-level engagement to leads that actually have a
  // campaign — otherwise a lead engaged through another channel would
  // inflate the campaign-funnel numbers.
  const leadsWithCampaign = new Set(campaigns.map((c) => c.lead_id));
  let opened = 0;
  let clicked = 0;
  for (const lead of engagedLeads) {
    if (!leadsWithCampaign.has(lead.id)) continue;
    // Moderation freeze: a contatto's engagement doesn't count toward the
    // tenant's open/click rate until the operator promotes it.
    if (moderated && !lead.operator_released_at) continue;
    if (lead.outreach_opened_at) opened += 1;
    if (lead.outreach_clicked_at) clicked += 1;
  }

  return {
    total,
    delivered,
    opened,
    clicked,
    failed,
    delivery_rate: total ? delivered / total : 0,
    open_rate: delivered ? opened / delivered : 0,
    click_rate: delivered ? clicked / delivered : 0,
  };
}

/** Timeline of events for a single lead (newest first).
 *
 * `events` is partitioned by ``occurred_at`` — there is no
 * ``created_at`` column. We explicitly select the columns we render
 * so a partition added later without a column won't silently break
 * the type.
 */
export async function listEventsForLead(
  leadId: string,
  limit = 50,
): Promise<EventRow[]> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('events')
    .select('id, tenant_id, lead_id, event_type, event_source, payload, occurred_at')
    .eq('lead_id', leadId)
    .order('occurred_at', { ascending: false })
    .limit(limit);
  if (error) throw new Error(`listEventsForLead: ${error.message}`);
  return (data ?? []) as EventRow[];
}

/** Campaigns for a single lead, oldest first (step-1, step-2, step-3). */
export async function listCampaignsForLead(
  leadId: string,
): Promise<CampaignRow[]> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('outreach_sends')
    .select(CAMPAIGN_COLUMNS)
    .eq('lead_id', leadId)
    .order('sequence_step', { ascending: true });
  if (error) throw new Error(`listCampaignsForLead: ${error.message}`);
  return (data ?? []) as unknown as CampaignRow[];
}

// ---------------------------------------------------------------------------
// Detail
// ---------------------------------------------------------------------------

/** Full detail of a single outreach send, joined with key lead fields. */
export interface OutreachSendDetail {
  id: string;
  lead_id: string;
  tenant_id: string;
  channel: string;
  template_id: string | null;
  sequence_step: number;
  email_subject: string | null;
  email_message_id: string | null;
  status: string;
  sent_at: string | null;
  cost_cents: number;
  rendering_gif_url: string | null;
  rendering_video_url: string | null;
  // Static after-image snapshot at send time. Used as third-tier
  // fallback on the /invii detail hero when video + GIF are both
  // missing (e.g. CREATIVE_SKIP_REPLICATE bypassed Kling).
  rendering_image_url: string | null;
  inbox_id: string | null;
  experiment_id: string | null;
  experiment_variant: string | null;
  leads: {
    id: string;
    pipeline_status: string | null;
    outreach_delivered_at: string | null;
    outreach_opened_at: string | null;
    outreach_clicked_at: string | null;
    rendering_image_url: string | null;
    rendering_gif_url: string | null;
    rendering_video_url: string | null;
    portal_video_slug: string | null;
    subjects: {
      business_name: string | null;
      decision_maker_name: string | null;
      decision_maker_email: string | null;
    } | null;
  } | null;
}

const SEND_DETAIL_COLUMNS = `
  id, lead_id, tenant_id, channel, sequence_step, status,
  template_id, email_subject, email_message_id,
  sent_at, cost_cents, failure_reason,
  rendering_gif_url, rendering_video_url, rendering_image_url,
  inbox_id, experiment_id, experiment_variant,
  leads:leads(
    id, pipeline_status, operator_released_at,
    outreach_delivered_at, outreach_opened_at, outreach_clicked_at,
    rendering_image_url, rendering_gif_url, rendering_video_url,
    portal_video_slug,
    subjects:subjects(business_name, decision_maker_name, decision_maker_email)
  )
`.trim();

/**
 * Fetch a single outreach send by id, joined with the parent lead's
 * engagement timestamps and media URLs.
 *
 * Returns null when the send doesn't exist or belongs to another tenant
 * (RLS will hide it and Supabase returns an empty result).
 */
export async function getOutreachSendDetail(
  id: string,
): Promise<OutreachSendDetail | null> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('outreach_sends')
    .select(SEND_DETAIL_COLUMNS)
    .eq('id', id)
    .limit(1)
    .maybeSingle();
  if (error) return null;
  const send = data as unknown as OutreachSendDetail | null;
  // Moderation freeze: the send detail must not reveal opens/clicks of a
  // contatto the operator hasn't promoted yet.
  if (send && (await isModeratedTenant())) freezeLeadEngagement(send.leads);
  return send;
}
