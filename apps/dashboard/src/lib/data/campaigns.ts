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
import type {
  CampaignRow,
  CampaignWithLeadEngagement,
  EventRow,
} from '@/types/db';

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
  leads:leads(outreach_delivered_at, outreach_opened_at, outreach_clicked_at)
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
  return (data ?? []) as unknown as CampaignWithLeadEngagement[];
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

  const [campaignsRes, engagementRes] = await Promise.all([
    supabase.from('outreach_sends').select('status, lead_id'),
    // RLS scopes `leads` to the current tenant → we only see our rows.
    supabase
      .from('leads')
      .select('id, outreach_opened_at, outreach_clicked_at')
      .or('outreach_opened_at.not.is.null,outreach_clicked_at.not.is.null'),
  ]);

  if (campaignsRes.error) {
    throw new Error(`getCampaignDeliveryStats: ${campaignsRes.error.message}`);
  }
  if (engagementRes.error) {
    throw new Error(`getCampaignDeliveryStats: ${engagementRes.error.message}`);
  }

  const campaigns = campaignsRes.data ?? [];
  const engagedLeads = engagementRes.data ?? [];

  const total = campaigns.length;
  const delivered = campaigns.filter((c) => c.status === 'delivered').length;
  const failed = campaigns.filter((c) => c.status === 'failed').length;

  // Restrict the lead-level engagement to leads that actually have a
  // campaign — otherwise a lead engaged through another channel would
  // inflate the campaign-funnel numbers.
  const leadsWithCampaign = new Set(campaigns.map((c) => c.lead_id));
  let opened = 0;
  let clicked = 0;
  for (const lead of engagedLeads) {
    if (!leadsWithCampaign.has(lead.id)) continue;
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
