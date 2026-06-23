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
  /** Emails that actually went out (failed sends excluded). */
  sent: number;
  delivered: number;
  opened: number;
  clicked: number;
  replied: number;
}

/** A successfully-sent send. 'failed' (and any queued state) never reached the
 *  prospect, so it must not count toward `sent` — the open-rate denominator. */
const SENT_OK = new Set(['sent', 'delivered']);

/** Raw join row shape returned by the query below. */
interface RawSendRow {
  status: string;
  experiment_variant?: string | null;
  leads?: {
    id?: string;
    province?: string | null;
    score_tier?: string | null;
    // Engagement is recorded on the LEAD (portal/Resend tracking), NOT as a
    // send-row status — `outreach_sends.status` only ever holds sent/failed.
    outreach_opened_at?: string | null;
    outreach_clicked_at?: string | null;
    outreach_replied_at?: string | null;
  } | null;
}

/**
 * Aggregate raw send rows into per-(variant, province, tier) results.
 *
 * Pure (no I/O) so the counting contract is unit-testable. Two deliberate
 * choices, both matching `getCampaignDeliveryStats` (the /invii KPIs):
 *   - `sent` counts only SUCCESSFULLY-SENT rows (`SENT_OK`); failed sends are
 *     dropped entirely, so `opened / sent` is a rate over real recipients.
 *   - opened/clicked/replied are LEAD-level (distinct leads with the signal),
 *     read from the lead, because the send row never carries those states.
 *   - `delivered` == `sent`: there is no provider delivery webhook wired, so a
 *     successfully handed-off email is the best "delivered" signal we have.
 */
export function aggregateCampaignResults(rows: RawSendRow[]): CampaignResultRow[] {
  interface Acc {
    variant: string | null;
    province: string | null;
    score_tier: string | null;
    sent: number;
    /** Distinct successfully-sent leads, keyed by id, for lead-level rates. */
    leads: Map<string, NonNullable<RawSendRow['leads']>>;
  }
  const map = new Map<string, Acc>();

  for (const row of rows) {
    if (!SENT_OK.has(row.status)) continue; // failed / queued → never sent
    const lead = row.leads ?? null;
    const variant = row.experiment_variant ?? null;
    const province = lead?.province ?? null;
    const score_tier = lead?.score_tier ?? null;
    const key = `${variant}|${province}|${score_tier}`;

    const acc =
      map.get(key) ??
      ({ variant, province, score_tier, sent: 0, leads: new Map() } as Acc);
    acc.sent += 1;
    if (lead?.id) acc.leads.set(lead.id, lead);
    map.set(key, acc);
  }

  const out: CampaignResultRow[] = [];
  for (const acc of map.values()) {
    let opened = 0;
    let clicked = 0;
    let replied = 0;
    for (const lead of acc.leads.values()) {
      if (lead.outreach_opened_at) opened += 1;
      if (lead.outreach_clicked_at) clicked += 1;
      if (lead.outreach_replied_at) replied += 1;
    }
    out.push({
      variant: acc.variant,
      province: acc.province,
      score_tier: acc.score_tier,
      sent: acc.sent,
      delivered: acc.sent, // no delivery webhook → handed-off == delivered
      opened,
      clicked,
      replied,
    });
  }
  return out.sort((a, b) => b.sent - a.sent);
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

  // Join outreach_sends → leads on lead_id to get province + score_tier +
  // the lead-level engagement signals. PostgREST has no GROUP BY, so we
  // fetch up to 2000 sends and aggregate in JS (fine until volume is high
  // enough to warrant a dedicated PG function).
  const { data, error } = await supabase
    .from('outreach_sends')
    .select(
      'status, experiment_variant, ' +
        'leads!inner(id, province, score_tier, outreach_opened_at, outreach_clicked_at, outreach_replied_at)',
    )
    .eq('acquisition_campaign_id', campaignId)
    .eq('tenant_id', tenantId)
    .limit(2000);

  if (error || !data) {
    console.error('[campaign-results] error', error?.message);
    return [];
  }

  return aggregateCampaignResults(data as unknown as RawSendRow[]);
}
