/**
 * Acquisition campaigns — server-side data access for strategic targeting entities.
 *
 * Each acquisition campaign is a named, reusable targeting strategy that
 * bundles the 5 wizard module configs (sorgente, tecnico, economico,
 * outreach, crm) with optional inbox restrictions and a monthly budget.
 *
 * Related: individual send records live in `outreach_sends` (see campaigns.ts).
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';
import type { AcquisitionCampaignRow } from '@/types/db';

const ACQ_COLUMNS = `
  id, tenant_id, name, description, is_default, status,
  sorgente_config, tecnico_config, economico_config, outreach_config, crm_config,
  inbox_ids, schedule_cron, budget_cap_cents,
  custom_copy_override,
  created_at, updated_at
`.trim();

/** All acquisition campaigns for the tenant, oldest first. */
export async function listAcquisitionCampaigns(): Promise<AcquisitionCampaignRow[]> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('acquisition_campaigns')
    .select(ACQ_COLUMNS)
    .order('created_at', { ascending: true });
  if (error) throw new Error(`listAcquisitionCampaigns: ${error.message}`);
  return (data ?? []) as unknown as AcquisitionCampaignRow[];
}

/** Fetch a single acquisition campaign by id. Returns null if not found. */
export async function getAcquisitionCampaign(
  id: string,
): Promise<AcquisitionCampaignRow | null> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('acquisition_campaigns')
    .select(ACQ_COLUMNS)
    .eq('id', id)
    .limit(1)
    .single();
  if (error) return null;
  return data as unknown as AcquisitionCampaignRow;
}

/** The tenant's default campaign (is_default=true). Returns null if none exists yet. */
export async function getDefaultCampaign(): Promise<AcquisitionCampaignRow | null> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('acquisition_campaigns')
    .select(ACQ_COLUMNS)
    .eq('is_default', true)
    .limit(1)
    .maybeSingle();
  if (error) return null;
  return data as AcquisitionCampaignRow | null;
}

/**
 * Aggregate send stats for one acquisition campaign.
 *
 * Reads from `outreach_sends` filtered by `acquisition_campaign_id`.
 * Returns zero-counts if there are no sends yet.
 */
export async function getCampaignSendStats(campaignId: string): Promise<{
  total: number;
  sent: number;
  delivered: number;
  failed: number;
}> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('outreach_sends')
    .select('status')
    .eq('acquisition_campaign_id', campaignId);

  if (error || !data) return { total: 0, sent: 0, delivered: 0, failed: 0 };

  const total = data.length;
  const sent = data.filter((r) => r.status === 'sent').length;
  const delivered = data.filter((r) => r.status === 'delivered').length;
  const failed = data.filter((r) => r.status === 'failed').length;
  return { total, sent, delivered, failed };
}
