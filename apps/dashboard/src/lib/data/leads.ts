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
                    decision_maker_email, decision_maker_email_verified,
                    decision_maker_phone, decision_maker_phone_source,
                    decision_maker_role, ateco_code, ateco_description,
                    yearly_revenue_cents, employees, linkedin_url,
                    sede_operativa_address, sede_operativa_cap,
                    sede_operativa_city, sede_operativa_province,
                    sede_operativa_lat, sede_operativa_lng,
                    sede_operativa_source),
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
  lat, lng, status, raw_data, derivations
`.trim();

const DETAIL_COLUMNS = `
  id, public_slug, pipeline_status, score, score_tier,
  outreach_channel, outreach_sent_at, outreach_opened_at,
  dashboard_visited_at, created_at,
  engagement_score, engagement_score_updated_at,
  portal_sessions, portal_total_time_sec, deepest_scroll_pct,
  subjects:subjects(type, business_name, owner_first_name, owner_last_name,
                    decision_maker_email, decision_maker_email_verified,
                    decision_maker_phone, decision_maker_phone_source,
                    decision_maker_role, ateco_code, ateco_description,
                    yearly_revenue_cents, employees, linkedin_url,
                    sede_operativa_address, sede_operativa_cap,
                    sede_operativa_city, sede_operativa_province,
                    sede_operativa_lat, sede_operativa_lng,
                    sede_operativa_source),
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

/**
 * Sprint C.3 — Sector signal for a lead.
 *
 * The hunter funnel stamps `predicted_sector` + `sector_confidence` on
 * `scan_candidates` at L1 (and `predicted_ateco_codes` at L3).
 *
 * For v2 leads: joins via `subjects.vat_number = scan_candidates.vat_number`.
 * For v3 leads: joins via `subjects.raw_data.scan_candidate_id` (stored by L6).
 *
 * Returns `null` when no scan_candidate row can be found.
 */
export interface LeadSectorSignal {
  predicted_sector: string | null;
  sector_confidence: number | null;
  predicted_ateco_codes: string[];
}

export async function getLeadSectorSignal(
  leadId: string,
): Promise<LeadSectorSignal | null> {
  const supabase = await createSupabaseServerClient();
  // Fetch subject join data: both vat_number (v2) and raw_data (v3 has scan_candidate_id)
  const { data: leadRow } = await supabase
    .from('leads')
    .select('subjects:subjects(vat_number, raw_data)')
    .eq('id', leadId)
    .maybeSingle();
  const subject = leadRow?.subjects as {
    vat_number?: string | null;
    raw_data?: Record<string, unknown> | null;
  } | null;
  if (!subject) return null;

  // v3 path: scan_candidate_id stored in subjects.raw_data by L6
  const scanCandidateId =
    typeof subject.raw_data?.scan_candidate_id === 'string'
      ? subject.raw_data.scan_candidate_id
      : null;

  if (scanCandidateId) {
    const { data: scanRaw } = await supabase
      .from('scan_candidates')
      .select('predicted_sector, sector_confidence, predicted_ateco_codes')
      .eq('id', scanCandidateId)
      .maybeSingle();
    if (!scanRaw) return null;
    return _parseSectorSignal(scanRaw as unknown as Record<string, unknown>);
  }

  // v2 path: join via vat_number
  const vat = subject.vat_number;
  if (!vat) return null;

  const { data: scanRaw } = await supabase
    .from('scan_candidates')
    .select('predicted_sector, sector_confidence, predicted_ateco_codes')
    .eq('vat_number', vat)
    .order('stage', { ascending: false })
    .limit(1)
    .maybeSingle();
  if (!scanRaw) return null;
  return _parseSectorSignal(scanRaw as unknown as Record<string, unknown>);
}

function _parseSectorSignal(
  scan: Record<string, unknown>,
): LeadSectorSignal {
  return {
    predicted_sector: (scan.predicted_sector as string | null) ?? null,
    sector_confidence:
      typeof scan.sector_confidence === 'number'
        ? (scan.sector_confidence as number)
        : scan.sector_confidence != null
          ? Number(scan.sector_confidence)
          : null,
    predicted_ateco_codes: Array.isArray(scan.predicted_ateco_codes)
      ? (scan.predicted_ateco_codes as string[])
      : [],
  };
}

// ---------------------------------------------------------------------------
// Sprint 8 — v3 Geocentric funnel intelligence signal
//
// For leads created by FLUSSO 1 v3, fetches the rich signals the funnel
// stamped on scan_candidates: Google Maps link, building quality score (0-5
// MVP heuristics), proxy score breakdown (Haiku L5), and scraped website URL.
//
// Returns null for legacy (v2) leads — the caller shows a graceful fallback.
// ---------------------------------------------------------------------------

export interface LeadV3Signal {
  funnel_version: 3;
  google_place_id: string | null;
  google_maps_url: string | null;
  building_quality_score: number | null;
  proxy_score_data: {
    icp_fit_score?: number | null;
    building_quality_score?: number | null;
    solar_potential_score?: number | null;
    contact_completeness_score?: number | null;
    overall_score?: number | null;
    predicted_size_category?: string | null;
    reasoning?: string | null;
  } | null;
  website_url: string | null;
  predicted_sector: string | null;
  sector_confidence: number | null;
  predicted_ateco_codes: string[];
}

export async function getLeadV3Signal(
  leadId: string,
): Promise<LeadV3Signal | null> {
  const supabase = await createSupabaseServerClient();

  // Step 1 — lead → subject.raw_data to locate the scan_candidate_id
  const { data: leadRow } = await supabase
    .from('leads')
    .select('subjects:subjects(raw_data)')
    .eq('id', leadId)
    .maybeSingle();
  const rawData = (
    leadRow?.subjects as { raw_data?: Record<string, unknown> | null } | null
  )?.raw_data;

  if (!rawData || rawData.source !== 'funnel_v3') return null;
  const scanCandidateId =
    typeof rawData.scan_candidate_id === 'string'
      ? rawData.scan_candidate_id
      : null;
  if (!scanCandidateId) return null;

  // Step 2 — fetch the scan_candidate row with v3 columns.
  // Cast to Record<string,unknown> because the Supabase generated types were
  // written before migration 0105 added the v3 columns.
  const { data: scRaw } = await supabase
    .from('scan_candidates')
    .select(
      'google_place_id, building_quality_score, proxy_score_data, scraped_data, ' +
      'predicted_sector, sector_confidence, predicted_ateco_codes',
    )
    .eq('id', scanCandidateId)
    .maybeSingle();
  if (!scRaw) return null;
  const sc = scRaw as unknown as Record<string, unknown>;

  const placeId = (sc.google_place_id as string | null) ?? null;
  const googleMapsUrl = placeId
    ? `https://www.google.com/maps/search/?api=1&query=Google&query_place_id=${placeId}`
    : null;

  // website_url lives in scraped_data.website or scraped_data.website_url
  const scraped = (sc.scraped_data as Record<string, unknown> | null) ?? {};
  const websiteUrl =
    (scraped.website_url as string | null) ??
    (scraped.website as string | null) ??
    null;

  const proxyRaw = (sc.proxy_score_data as Record<string, unknown> | null) ?? null;

  return {
    funnel_version: 3,
    google_place_id: placeId,
    google_maps_url: googleMapsUrl,
    building_quality_score:
      typeof sc.building_quality_score === 'number'
        ? (sc.building_quality_score as number)
        : sc.building_quality_score != null
          ? Number(sc.building_quality_score)
          : null,
    proxy_score_data: proxyRaw
      ? {
          icp_fit_score: proxyRaw.icp_fit_score as number | null,
          building_quality_score: proxyRaw.building_quality_score as number | null,
          solar_potential_score: proxyRaw.solar_potential_score as number | null,
          contact_completeness_score: proxyRaw.contact_completeness_score as number | null,
          overall_score: proxyRaw.overall_score as number | null,
          predicted_size_category: proxyRaw.predicted_size_category as string | null,
          reasoning: proxyRaw.reasoning as string | null,
        }
      : null,
    website_url: websiteUrl,
    predicted_sector: (sc.predicted_sector as string | null) ?? null,
    sector_confidence:
      typeof sc.sector_confidence === 'number'
        ? (sc.sector_confidence as number)
        : sc.sector_confidence != null
          ? Number(sc.sector_confidence)
          : null,
    predicted_ateco_codes: Array.isArray(sc.predicted_ateco_codes)
      ? (sc.predicted_ateco_codes as string[])
      : [],
  };
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
 * "Caldi adesso senza risposta" — leads who have engaged with the
 * portal recently (real-time engagement_score bumped via the public
 * track endpoint, see migration 0066) and have NOT yet replied or
 * been moved into a closing pipeline stage.
 *
 * This is the operator's call-list: high engagement, recent activity,
 * no contact yet. Used by both the /leads "Caldi adesso" filter and
 * the overview hot-leads widget.
 *
 * Excludes pipeline_status IN (engaged, whatsapp, appointment,
 * closed_won, closed_lost, blacklisted) so the operator only sees
 * leads that haven't been "claimed" in any direction yet.
 */
const HOT_AWAITING_EXCLUDED: LeadStatus[] = [
  'engaged',
  'whatsapp',
  'appointment',
  'closed_won',
  'closed_lost',
  'blacklisted',
];

export async function listHotLeadsAwaitingResponse(opts: {
  sinceHours?: number;
  minScore?: number;
  limit?: number;
} = {}): Promise<LeadListRow[]> {
  const sinceHours = opts.sinceHours ?? 72;
  const minScore = opts.minScore ?? 60;
  const limit = opts.limit ?? 25;
  const supabase = await createSupabaseServerClient();
  const cutoff = new Date(Date.now() - sinceHours * 60 * 60 * 1000).toISOString();

  try {
    const { data, error } = await supabase
      .from('leads')
      .select(LIST_COLUMNS)
      .gte('engagement_score', minScore)
      .gte('last_portal_event_at', cutoff)
      .not('pipeline_status', 'in', `(${HOT_AWAITING_EXCLUDED.join(',')})`)
      .order('engagement_score', { ascending: false })
      .order('last_portal_event_at', { ascending: false })
      .limit(limit);

    // Graceful fallback if migration 0066 (last_portal_event_at column) has
    // not yet been applied to the database — widget shows empty instead of
    // crashing the dashboard home page.
    if (error) {
      console.warn('listHotLeadsAwaitingResponse skipped (DB migration pending):', error.message);
      return [];
    }
    return (data ?? []) as unknown as LeadListRow[];
  } catch (err) {
    console.warn('listHotLeadsAwaitingResponse caught unexpected error:', err);
    return [];
  }
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
