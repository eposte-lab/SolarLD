/**
 * Leads data access — all server-side, all RLS-scoped.
 *
 * The dashboard reads from Supabase directly (the service role key
 * never leaves the FastAPI container). RLS on `leads`/`subjects`/
 * `roofs` ensures every row returned belongs to the current tenant,
 * so we never need to pass `tenant_id` explicitly.
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';
import type {
  LeadDetailRow,
  LeadListRow,
  LeadScoreTier,
  LeadStatus,
  OverviewKpis,
} from '@/types/db';

/** Default page size for the leads list. */
export const LEADS_PAGE_SIZE = 25;

const LIST_COLUMNS = `
  id, public_slug, pipeline_status, score, score_tier,
  outreach_channel, outreach_sent_at, outreach_opened_at,
  dashboard_visited_at, created_at,
  engagement_score, engagement_score_updated_at,
  portal_sessions, portal_total_time_sec, deepest_scroll_pct,
  subjects:subjects(type, business_name, owner_first_name, owner_last_name,
                    decision_maker_email, decision_maker_email_verified),
  roofs:roofs(address, comune, provincia, cap,
              estimated_kwp, estimated_yearly_kwh, area_sqm)
`.trim();

// Detail page also pulls Solar API fields from `roofs` so the
// "Dati Solar API" inspection panel can show panel count, dominant
// azimuth, pitch, shading and the raw payload — used by the operator
// to sanity-check the quote before sending.
const DETAIL_ROOF_COLUMNS = `
  address, comune, provincia, cap,
  estimated_kwp, estimated_yearly_kwh, area_sqm,
  exposure, pitch_degrees, shading_score, has_existing_pv,
  lat, lng, status, raw_data
`.trim();

const DETAIL_COLUMNS = `
  id, public_slug, pipeline_status, score, score_tier,
  outreach_channel, outreach_sent_at, outreach_opened_at,
  dashboard_visited_at, created_at,
  engagement_score, engagement_score_updated_at,
  portal_sessions, portal_total_time_sec, deepest_scroll_pct,
  subjects:subjects(type, business_name, owner_first_name, owner_last_name,
                    decision_maker_email, decision_maker_email_verified),
  roofs:roofs(${DETAIL_ROOF_COLUMNS}),
  rendering_image_url, rendering_video_url, rendering_gif_url, portal_video_slug,
  roi_data, outreach_delivered_at, outreach_clicked_at,
  whatsapp_initiated_at, feedback, feedback_notes, score_breakdown
`.trim();

export interface LeadListFilter {
  status?: LeadStatus;
  tier?: LeadScoreTier;
  q?: string; // free-text on business_name / owner_last_name
}

export interface LeadListResult {
  rows: LeadListRow[];
  total: number;
}

/** Paginated, filtered list of leads. Ordered by score DESC. */
export async function listLeads(opts: {
  page?: number;
  pageSize?: number;
  filter?: LeadListFilter;
} = {}): Promise<LeadListResult> {
  const page = Math.max(1, opts.page ?? 1);
  const pageSize = opts.pageSize ?? LEADS_PAGE_SIZE;
  const from = (page - 1) * pageSize;
  const to = from + pageSize - 1;

  const supabase = await createSupabaseServerClient();
  let q = supabase.from('leads').select(LIST_COLUMNS, { count: 'exact' });

  if (opts.filter?.status) q = q.eq('pipeline_status', opts.filter.status);
  if (opts.filter?.tier) q = q.eq('score_tier', opts.filter.tier);

  // Free-text: matches business_name OR owner_last_name via a loose ilike.
  // Doing it as two separate PostgREST `or` clauses on joined columns is
  // verbose; for now we just match on `leads.public_slug` as a fallback.
  if (opts.filter?.q && opts.filter.q.trim()) {
    q = q.ilike('public_slug', `%${opts.filter.q.trim()}%`);
  }

  const { data, error, count } = await q
    .order('score', { ascending: false })
    .range(from, to);

  if (error) throw new Error(`listLeads: ${error.message}`);
  return { rows: (data ?? []) as unknown as LeadListRow[], total: count ?? 0 };
}

/** Single lead by id — used on the detail page. Returns null if not found. */
export async function getLeadById(id: string): Promise<LeadDetailRow | null> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('leads')
    .select(DETAIL_COLUMNS)
    .eq('id', id)
    .maybeSingle();
  if (error) throw new Error(`getLeadById: ${error.message}`);
  return (data ?? null) as unknown as LeadDetailRow | null;
}

/** Top-N hot leads for the overview widget. */
export async function listTopHotLeads(limit = 10): Promise<LeadListRow[]> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('leads')
    .select(LIST_COLUMNS)
    .eq('score_tier', 'hot')
    .order('score', { ascending: false })
    .limit(limit);
  if (error) throw new Error(`listTopHotLeads: ${error.message}`);
  return (data ?? []) as unknown as LeadListRow[];
}

/**
 * Overview KPIs — 4 counters for the home page.
 *
 * We issue 4 small `count`-only queries in parallel rather than
 * one big GROUP BY, because RLS on `leads` already restricts the
 * working set to the current tenant and PostgREST counting is
 * cheap at this scale.
 */
export async function getOverviewKpis(): Promise<OverviewKpis> {
  const supabase = await createSupabaseServerClient();
  const since = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();

  const [sent, hot, appointments, closedWon] = await Promise.all([
    supabase
      .from('leads')
      .select('id', { count: 'exact', head: true })
      .not('outreach_sent_at', 'is', null)
      .gte('outreach_sent_at', since),
    supabase
      .from('leads')
      .select('id', { count: 'exact', head: true })
      .eq('score_tier', 'hot'),
    supabase
      .from('leads')
      .select('id', { count: 'exact', head: true })
      .eq('pipeline_status', 'appointment')
      .gte('created_at', since),
    supabase
      .from('leads')
      .select('id', { count: 'exact', head: true })
      .eq('pipeline_status', 'closed_won')
      .gte('created_at', since),
  ]);

  return {
    leads_sent_30d: sent.count ?? 0,
    hot_leads: hot.count ?? 0,
    appointments_30d: appointments.count ?? 0,
    closed_won_30d: closedWon.count ?? 0,
  };
}
