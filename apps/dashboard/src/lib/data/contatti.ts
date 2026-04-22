/**
 * Contatti data access — scan_candidates, server-only, RLS-scoped.
 *
 * scan_candidates is the top-of-funnel working set: every company
 * discovered by the B2B Atoka discovery (L1) through Solar qualification
 * (L4). These are NOT yet leads — they become leads only after the
 * ScoringAgent promotes them.
 *
 * Terminology:
 *   stage 1 = scoperto da Atoka (L1)
 *   stage 2 = arricchito via Places (L2)
 *   stage 3 = scored da Haiku (L3)
 *   stage 4 = qualificato Solar (L4)
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';

export const CONTATTI_PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type SolarVerdict =
  | 'accepted'
  | 'rejected_tech'
  | 'no_building'
  | 'api_error'
  | 'skipped_below_gate';

export interface ContattoRow {
  id: string;
  scan_id: string;
  territory_id: string;
  vat_number: string | null;
  business_name: string | null;
  ateco_code: string | null;
  employees: number | null;
  revenue_eur: number | null;
  hq_city: string | null;
  hq_province: string | null;
  score: number | null; // L3 Haiku score
  stage: number; // 1-4
  solar_verdict: SolarVerdict | null;
  created_at: string;
  territories: { name: string; type: string; code: string } | null;
}

export interface ContattoListResult {
  rows: ContattoRow[];
  total: number;
}

export interface ContattiSummary {
  l1: number;
  l2: number;
  l3: number;
  l4_qualified: number;    // solar_verdict = 'accepted'
  l4_rejected: number;     // solar_verdict = 'rejected_tech'
  l4_no_building: number;
  l4_skipped: number;      // solar_verdict = 'skipped_below_gate'
  total: number;           // = l1 (all stage >= 1)
}

export interface ScanFunnelData {
  discovery: {
    l1: number;
    l2: number;
    l3: number;
    l4_qualified: number;
    l4_rejected: number;
    l4_skipped: number;
  };
  pipeline: {
    leads_total: number;
    sent: number;
    delivered: number;
    opened: number;
    clicked: number;
    engaged: number;
    appointment: number;
    won: number;
    conversions_won: number;
  };
  cost: {
    total_scan_cost_cents: number;
    cost_per_contact_cents: number | null;
    cost_per_lead_cents: number | null;
    cost_per_sent_cents: number | null;
  };
}

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

const LIST_COLUMNS = `
  id, scan_id, territory_id, vat_number, business_name,
  ateco_code, employees, revenue_eur, hq_city, hq_province,
  score, stage, solar_verdict, created_at,
  territories:territory_id(name, type, code)
`.trim();

export interface ContattiFilter {
  stage?: number;
  territory_id?: string;
  solar_verdict?: SolarVerdict;
}

/** Paginated list of scan_candidates. */
export async function listContatti(opts: {
  page?: number;
  filter?: ContattiFilter;
} = {}): Promise<ContattoListResult> {
  const page = Math.max(1, opts.page ?? 1);
  const from = (page - 1) * CONTATTI_PAGE_SIZE;
  const to = from + CONTATTI_PAGE_SIZE - 1;

  const sb = await createSupabaseServerClient();
  let q = sb
    .from('scan_candidates')
    .select(LIST_COLUMNS, { count: 'exact' });

  if (opts.filter?.stage != null) q = q.eq('stage', opts.filter.stage);
  if (opts.filter?.territory_id) q = q.eq('territory_id', opts.filter.territory_id);
  if (opts.filter?.solar_verdict) q = q.eq('solar_verdict', opts.filter.solar_verdict);

  const { data, error, count } = await q
    .order('created_at', { ascending: false })
    .range(from, to);

  if (error) throw new Error(`listContatti: ${error.message}`);
  return {
    rows: (data ?? []) as unknown as ContattoRow[],
    total: count ?? 0,
  };
}

/** Stage counts for the header summary strip. */
export async function getContattiSummary(): Promise<ContattiSummary> {
  const sb = await createSupabaseServerClient();

  const countGte = async (minStage: number): Promise<number> => {
    const { count, error } = await sb
      .from('scan_candidates')
      .select('id', { count: 'exact', head: true })
      .gte('stage', minStage);
    if (error) return 0;
    return count ?? 0;
  };

  const countVerdict = async (verdict: SolarVerdict): Promise<number> => {
    const { count, error } = await sb
      .from('scan_candidates')
      .select('id', { count: 'exact', head: true })
      .eq('stage', 4)
      .eq('solar_verdict', verdict);
    if (error) return 0;
    return count ?? 0;
  };

  const [l1, l2, l3, l4_qualified, l4_rejected, l4_no_building, l4_skipped] =
    await Promise.all([
      countGte(1),
      countGte(2),
      countGte(3),
      countVerdict('accepted'),
      countVerdict('rejected_tech'),
      countVerdict('no_building'),
      countVerdict('skipped_below_gate'),
    ]);

  return {
    l1,
    l2,
    l3,
    l4_qualified,
    l4_rejected,
    l4_no_building,
    l4_skipped,
    total: l1,
  };
}

/** Full waterfall for the /funnel page (discovery + pipeline + cost). */
export async function getScanFunnel(): Promise<ScanFunnelData> {
  const sb = await createSupabaseServerClient();

  const countSc = async (filters: Record<string, unknown> & { gteStage?: number }): Promise<number> => {
    let q = sb.from('scan_candidates').select('id', { count: 'exact', head: true });
    if (filters.gteStage != null) q = q.gte('stage', filters.gteStage as number);
    if (filters.stage != null) q = q.eq('stage', filters.stage as number);
    if (filters.solar_verdict != null) q = q.eq('solar_verdict', filters.solar_verdict as string);
    const { count, error } = await q;
    if (error) return 0;
    return count ?? 0;
  };

  const countLead = async (col?: string, val?: string): Promise<number> => {
    let q = sb.from('leads').select('id', { count: 'exact', head: true });
    if (col && val) q = q.eq(col, val);
    const { count, error } = await q;
    if (error) return 0;
    return count ?? 0;
  };

  const countLeadNotNull = async (col: string): Promise<number> => {
    const { count, error } = await sb
      .from('leads')
      .select('id', { count: 'exact', head: true })
      .not(col, 'is', null);
    if (error) return 0;
    return count ?? 0;
  };

  const countConversion = async (stage: string): Promise<number> => {
    const { count, error } = await sb
      .from('conversions')
      .select('id', { count: 'exact', head: true })
      .eq('stage', stage);
    if (error) return 0;
    return count ?? 0;
  };

  // Aggregate scan costs from events
  const scanCostCents = async (): Promise<number> => {
    const { data, error } = await sb
      .from('events')
      .select('payload')
      .eq('event_type', 'scan.completed')
      .order('occurred_at', { ascending: false })
      .limit(200);
    if (error || !data) return 0;
    return data.reduce((sum, row) => {
      const p = row.payload as Record<string, unknown>;
      return sum + Number(p?.total_cost_cents ?? 0);
    }, 0);
  };

  const [
    l1, l2, l3, l4_qualified, l4_rejected, l4_skipped,
    leads_total, sent, delivered, opened, clicked, engaged,
    appointment, won, conversions_won, totalScanCost,
  ] = await Promise.all([
    countSc({ gteStage: 1 }),
    countSc({ gteStage: 2 }),
    countSc({ gteStage: 3 }),
    countSc({ stage: 4, solar_verdict: 'accepted' }),
    countSc({ stage: 4, solar_verdict: 'rejected_tech' }),
    countSc({ stage: 4, solar_verdict: 'skipped_below_gate' }),
    countLead(),
    countLeadNotNull('outreach_sent_at'),
    countLeadNotNull('outreach_delivered_at'),
    countLeadNotNull('outreach_opened_at'),
    countLeadNotNull('outreach_clicked_at'),
    countLead('pipeline_status', 'engaged'),
    countLead('pipeline_status', 'appointment'),
    countLead('pipeline_status', 'closed_won'),
    countConversion('won'),
    scanCostCents(),
  ]);

  return {
    discovery: { l1, l2, l3, l4_qualified, l4_rejected, l4_skipped },
    pipeline: { leads_total, sent, delivered, opened, clicked, engaged, appointment, won, conversions_won },
    cost: {
      total_scan_cost_cents: totalScanCost,
      cost_per_contact_cents: l1 > 0 ? Math.round(totalScanCost / l1) : null,
      cost_per_lead_cents: leads_total > 0 ? Math.round(totalScanCost / leads_total) : null,
      cost_per_sent_cents: sent > 0 ? Math.round(totalScanCost / sent) : null,
    },
  };
}
