/**
 * Campaign results — server-side aggregation of send performance
 * grouped by variant / zone / target segment.
 *
 * Used by the "Risultati" tab in /campaigns/[id].
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';

export interface CampaignResultRow {
  /** A/B experiment variant label, or null for the control group. */
  variant: string | null;
  province: string | null;
  score_tier: string | null;
  sent: number;
  delivered: number;
  opened: number;
  clicked: number;
  replied: number;
}

/**
 * Aggregate send performance for one acquisition campaign.
 *
 * Groups by (experiment_variant, province from leads, score_tier from leads).
 * Falls back to empty array if the outreach_sends table has no rows yet.
 */
export async function getCampaignResults(
  campaignId: string,
  tenantId: string,
): Promise<CampaignResultRow[]> {
  const supabase = await createSupabaseServerClient();

  // Join outreach_sends → leads on lead_id to get province + score_tier.
  // Supabase PostgREST doesn't support GROUP BY directly, so we use
  // a raw RPC or build client-side aggregation from the raw rows.
  // For now fetch up to 2000 sends and aggregate in JS — acceptable
  // until volume is high enough to warrant a dedicated PG function.
  const { data, error } = await supabase
    .from('outreach_sends')
    .select(
      'status, experiment_variant, leads!inner(province, score_tier)',
    )
    .eq('acquisition_campaign_id', campaignId)
    .eq('tenant_id', tenantId)
    .limit(2000);

  if (error || !data) {
    console.error('[campaign-results] error', error?.message);
    return [];
  }

  // Aggregate client-side.
  const map = new Map<string, CampaignResultRow>();

  for (const row of data) {
    const lead = (row as unknown as { leads?: { province?: string; score_tier?: string } }).leads;
    const variant = (row as unknown as { experiment_variant?: string }).experiment_variant ?? null;
    const province = lead?.province ?? null;
    const score_tier = lead?.score_tier ?? null;
    const key = `${variant}|${province}|${score_tier}`;

    const existing = map.get(key) ?? {
      variant,
      province,
      score_tier,
      sent: 0,
      delivered: 0,
      opened: 0,
      clicked: 0,
      replied: 0,
    };

    const status = (row as unknown as { status: string }).status;
    existing.sent += 1;
    if (status === 'delivered') existing.delivered += 1;
    if (status === 'opened') existing.opened += 1;
    if (status === 'clicked') existing.clicked += 1;
    if (status === 'replied' || status === 'engaged') existing.replied += 1;

    map.set(key, existing);
  }

  return Array.from(map.values()).sort(
    (a, b) => b.sent - a.sent,
  );
}
