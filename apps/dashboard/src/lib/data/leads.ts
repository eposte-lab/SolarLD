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
  outreach_delivered_at, outreach_clicked_at, outreach_replied_at,
  whatsapp_initiated_at, dashboard_visited_at, created_at,
  engagement_score, engagement_score_updated_at, engagement_peak_score,
  last_portal_event_at,
  last_followup_scenario, last_followup_sent_at, hot_lead_alerted_at,
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
              estimated_kwp, estimated_yearly_kwh, area_sqm,
              territory_id)
`.trim();

// Detail page also pulls Solar API fields from `roofs` so the
// "Dati Solar API" inspection panel can show panel count, dominant
// azimuth, pitch, shading and the raw payload — used by the operator
// to sanity-check the quote before sending.
const DETAIL_ROOF_COLUMNS = `
  address, comune, provincia, cap,
  estimated_kwp, estimated_yearly_kwh, area_sqm,
  exposure, pitch_degrees, shading_score, has_existing_pv,
  lat, lng, status, raw_data, derivations, data_source,
  territory_id
`.trim();

const DETAIL_COLUMNS = `
  id, public_slug, pipeline_status, score, score_tier,
  outreach_channel, outreach_sent_at, outreach_opened_at,
  dashboard_visited_at, created_at,
  engagement_score, engagement_score_updated_at, engagement_peak_score,
  last_portal_event_at,
  last_followup_scenario, last_followup_sent_at, hot_lead_alerted_at,
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
  creative_skipped_reason,
  roi_data, outreach_delivered_at, outreach_clicked_at,
  whatsapp_initiated_at, feedback, feedback_notes, score_breakdown
`.trim();

export interface LeadListFilter {
  status?: LeadStatus;
  tier?: LeadScoreTier;
  q?: string; // free-text on business_name / owner_last_name
  /** Activity filters — answer "did the lead do X?" without needing
   *  to open the detail page. Multiple flags AND together: `read=true`
   *  + `clicked=true` returns leads that opened AND clicked. */
  read?: boolean | null; // outreach_opened_at: true=read, false=not, null=any
  clicked?: boolean | null; // outreach_clicked_at
  portalVisited?: boolean | null; // last_portal_event_at OR dashboard_visited_at
  /** Follow-up management mode:
   *    'manual' → engagement_score >= SCORE_HOT_MIN (61): system handed
   *               off, operator has to take over manually.
   *    'auto'   → engagement_score < 61 (or null): system still
   *               sending tiered follow-ups on its own.
   *  null/undefined → no filter applied. */
  management?: 'auto' | 'manual';
  /** Filter leads by origin territory (tenant_target_areas.id). Used by
   *  the "Territorio" chip on /leads/[id] — clicking the chip drives
   *  the list to show all leads from the same OSM zone. */
  territoryId?: string;
}

export interface LeadListResult {
  rows: LeadListRow[];
  total: number;
}

/**
 * PostgREST `.or()` clauses that flag a lead as "engaged" — i.e. the
 * prospect has taken at least one concrete action. Email open alone
 * (`outreach_opened_at`) does NOT count: too passive (firewall pre-fetch
 * + tracking-pixel false positives). Used by `listLeads()` so that
 * /leads only shows real prospects, while pre-engagement candidates
 * stay in /contatti.
 *
 * Implementation note: PostgREST's `.or()` parser treats commas as
 * clause delimiters, so `status.in.(a,b,c)` with inner commas produces
 * a parse error at server side. We work around it by emitting one
 * `eq` clause per engaged status — verbose but unambiguous.
 */
const ENGAGED_PIPELINE_STATUSES = [
  'clicked',
  'engaged',
  'whatsapp',
  'appointment',
  'closed_won',
  'closed_lost',
] as const;

const ENGAGEMENT_OR = [
  'outreach_clicked_at.not.is.null',
  'dashboard_visited_at.not.is.null',
  'whatsapp_initiated_at.not.is.null',
  'outreach_replied_at.not.is.null',
  'portal_sessions.gt.0',
  // Server-side engagement signals: a portal_event written by the API
  // (bolletta upload, manual ops actions) bumps engagement_score and
  // last_portal_event_at via bump_engagement_score RPC, but doesn't
  // touch dashboard_visited_at (only the client VisitTracker does).
  // Without these two clauses such leads vanish from /leads even
  // though they're clearly engaged. Keeping both is defensive — either
  // one alone misses some scenarios (rollup-only sessions reset
  // engagement_score during the night, but last_portal_event_at stays).
  'engagement_score.gt.0',
  'last_portal_event_at.not.is.null',
  ...ENGAGED_PIPELINE_STATUSES.map((s) => `pipeline_status.eq.${s}`),
].join(',');

/** Paginated, filtered list of ENGAGED leads. Ordered by score DESC.
 *  Pre-engagement candidates (sent / delivered / opened only) live in
 *  /contatti and do not appear here. */
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
  // "Priorità" filtra per tier di ENGAGEMENT (comportamento reale), non
  // per lo score_tier ICP statico — così il filtro è coerente con il
  // chip engagement mostrato in tabella. Soglie allineate a
  // engagementTier(): hot >=60, warm 25-59, cold <25.
  if (opts.filter?.tier === 'hot') q = q.gte('engagement_score', 60);
  else if (opts.filter?.tier === 'warm')
    q = q.gte('engagement_score', 25).lt('engagement_score', 60);
  else if (opts.filter?.tier === 'cold') q = q.lt('engagement_score', 25);

  // Free-text: matches business_name OR owner_last_name via a loose ilike.
  // Doing it as two separate PostgREST `or` clauses on joined columns is
  // verbose; for now we just match on `leads.public_slug` as a fallback.
  if (opts.filter?.q && opts.filter.q.trim()) {
    q = q.ilike('public_slug', `%${opts.filter.q.trim()}%`);
  }

  // Activity filters — applied as AND on top of the engagement gate.
  // `true` requires the timestamp to exist; `false` requires it to be
  // null. The portal-visited filter widens to either of the two columns
  // because the API and the in-page tracker write to different ones.
  if (opts.filter?.read === true) q = q.not('outreach_opened_at', 'is', null);
  else if (opts.filter?.read === false) q = q.is('outreach_opened_at', null);
  if (opts.filter?.clicked === true) q = q.not('outreach_clicked_at', 'is', null);
  else if (opts.filter?.clicked === false) q = q.is('outreach_clicked_at', null);
  if (opts.filter?.portalVisited === true) {
    q = q.or(
      'last_portal_event_at.not.is.null,dashboard_visited_at.not.is.null',
    );
  } else if (opts.filter?.portalVisited === false) {
    q = q
      .is('last_portal_event_at', null)
      .is('dashboard_visited_at', null);
  }

  // Management filter — splits the list by who's driving the follow-up.
  // The threshold (61) mirrors `SCORE_HOT_MIN` in the Python service so
  // the UI's "Manuale" matches exactly the leads on which the cron has
  // already paused automatic emails (cron.py:1080-1120).
  if (opts.filter?.management === 'manual') {
    q = q.gte('engagement_score', 61);
  } else if (opts.filter?.management === 'auto') {
    q = q.or('engagement_score.lt.61,engagement_score.is.null');
  }

  // Territory filter — clicking the "Territorio" chip on the detail
  // page drives the list to all leads sourced from the same OSM zone.
  // The FK lives on roofs (roof_id → territory_id), so we navigate
  // through the join.
  if (opts.filter?.territoryId) {
    q = q.eq('roofs.territory_id', opts.filter.territoryId);
  }

  // Engagement gate — only leads with at least one concrete action.
  q = q.or(ENGAGEMENT_OR);

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
  // ─── Display fallbacks for /leads/[id] ──────────────────────────────
  // These come from the same scan_candidates row but are exposed
  // separately so the lead detail page can fall back to v3 data when
  // the legacy `subjects.*` / `roofs.*` columns are NULL (typical for
  // freshly-promoted v3 leads).
  display_name: string | null;          // enrichment.places.display_name
  formatted_address: string | null;     // enrichment.places.formatted_address
  best_email: string | null;            // contact_extraction.best_email
  best_phone: string | null;            // contact_extraction.decision_maker_phone ?? best_phone
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

  // Accept both 'funnel_v3' (live promotion) and 'funnel_v3_backfill'
  // (manual backfill of accepted scan_candidates that were missed by the
  // L6 promoter — same v3 signal shape, just promoted later).
  if (!rawData) return null;
  const src = typeof rawData.source === 'string' ? rawData.source : '';
  if (!src.startsWith('funnel_v3')) return null;
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
      'google_place_id, building_quality_score, proxy_score_data, scraped_data, enrichment, ' +
      'contact_extraction, predicted_sector, sector_confidence, predicted_ateco_codes',
    )
    .eq('id', scanCandidateId)
    .maybeSingle();
  if (!scRaw) return null;
  const sc = scRaw as unknown as Record<string, unknown>;

  const placeId = (sc.google_place_id as string | null) ?? null;
  const googleMapsUrl = placeId
    ? `https://www.google.com/maps/search/?api=1&query=Google&query_place_id=${placeId}`
    : null;

  // website_url resolution:
  //   1. scraped_data.website_url — explicit URL field (rare, set by future
  //      scraper versions that record the canonical URL).
  //   2. enrichment.places.website — the URL Google Places returned at L1.
  //      This is the canonical source for funnel-v3 leads.
  //   3. scraped_data.website — when set as a STRING (legacy v2 scraper
  //      that stored the URL directly here). For v3 this slot is an OBJECT
  //      `{pec, phone, emails, ...}` containing the contacts extracted from
  //      the page, NOT the URL — we must NOT cast that to a string or the
  //      page will crash with `.startsWith is not a function` later on.
  const scraped = (sc.scraped_data as Record<string, unknown> | null) ?? {};
  const enrichment = (sc.enrichment as Record<string, unknown> | null) ?? {};
  const placesBlob = (enrichment.places as Record<string, unknown> | null) ?? {};
  const websiteCandidate =
    (typeof scraped.website_url === 'string' ? scraped.website_url : null) ??
    (typeof placesBlob.website === 'string' ? placesBlob.website : null) ??
    (typeof scraped.website === 'string' ? scraped.website : null) ??
    null;
  const websiteUrl = websiteCandidate;

  const proxyRaw = (sc.proxy_score_data as Record<string, unknown> | null) ?? null;

  // ─── Display fallbacks ───────────────────────────────────────────
  // Pull what we can from places + contact_extraction so the lead
  // detail page can fill the Anagrafica/Tetto cards even when the
  // legacy subjects/roofs columns are NULL (fresh v3 leads).
  const displayName =
    typeof placesBlob.display_name === 'string' ? placesBlob.display_name : null;
  const formattedAddress =
    typeof placesBlob.formatted_address === 'string'
      ? placesBlob.formatted_address
      : null;
  const contactExtraction =
    (sc.contact_extraction as Record<string, unknown> | null) ?? {};
  const bestEmail =
    typeof contactExtraction.best_email === 'string'
      ? contactExtraction.best_email
      : null;
  // Decision-maker phone preferred over generic best_phone for B2B outreach.
  const bestPhone =
    (typeof contactExtraction.decision_maker_phone === 'string'
      ? contactExtraction.decision_maker_phone
      : null) ??
    (typeof contactExtraction.best_phone === 'string'
      ? contactExtraction.best_phone
      : null);

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
    display_name: displayName,
    formatted_address: formattedAddress,
    best_email: bestEmail,
    best_phone: bestPhone,
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
 * Excludes pipeline_status IN (whatsapp, appointment, closed_won,
 * closed_lost, blacklisted) — those mean the sales side has already
 * taken over. `engaged` stays in the list intentionally: that status
 * means the LEAD took action (portal visit, click) but no human has
 * reached out yet, which is exactly the call-list candidate the
 * widget is supposed to surface.
 */
const HOT_AWAITING_EXCLUDED: LeadStatus[] = [
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
  // Was 60 — too strict. A single portal.view bumps engagement by +5,
  // a scroll_50 by +3, a roi_viewed by +10 (see _EVENT_DELTA in
  // apps/api/src/routes/public.py). 60 required ~5 different actions
  // in the same session, so first-time visitors never qualified for
  // "Caldi adesso" and the operator saw an empty list. Threshold of 5
  // means "any portal activity counts" — the recent-event filter
  // (sinceHours=72) keeps the list scoped to actually-warm leads.
  const minScore = opts.minScore ?? 5;
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

  // "Hot leads" semantics for the operator: leads with proven
  // engagement, regardless of the AI score_tier prediction at import
  // time. Score_tier locks the counter to leads the AI predicted
  // hot, but what the sales team wants to call is leads who actually
  // engaged (portal session, email click, dashboard visit) — that's
  // what `engagement_score` measures.
  //
  // Threshold 50 covers: opened email + portal session, or
  // bolletta/whatsapp/appointment events on their own (each weighted
  // 20-30 — see engagement_service.py).

  const [sent, hot, conversionRows] = await Promise.all([
    supabase
      .from('leads')
      .select('id', { count: 'exact', head: true })
      .not('outreach_sent_at', 'is', null)
      .gte('outreach_sent_at', since),
    supabase
      .from('leads')
      .select('id', { count: 'exact', head: true })
      .gte('engagement_score', 50),
    // Appointments + closed_won come from the `conversions` table —
    // `closed_at` is the actual transition timestamp, while
    // `leads.created_at` is the lead's birthday and would silently drop
    // every old lead that signs today. The `conversions` table is
    // populated by the public pixel + POST endpoints and is the same
    // source `getConversionStats` reads on the funnel card below.
    supabase
      .from('conversions')
      .select('stage')
      .gte('closed_at', since),
  ]);

  let appointments_30d = 0;
  let closed_won_30d = 0;
  for (const row of conversionRows.data ?? []) {
    if (row.stage === 'booked') appointments_30d += 1;
    else if (row.stage === 'won') closed_won_30d += 1;
  }

  return {
    leads_sent_30d: sent.count ?? 0,
    hot_leads: hot.count ?? 0,
    appointments_30d,
    closed_won_30d,
  };
}
