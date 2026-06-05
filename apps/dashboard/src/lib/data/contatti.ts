/**
 * Contatti data access — scan_candidates, server-only, RLS-scoped.
 *
 * scan_candidates is the top-of-funnel working set: every company
 * discovered by the v3 funnel (L1 Google Places → L4 Solar API → L5
 * Haiku scoring). The /contatti page shows only candidates that
 * survived L4 (`solar_verdict='accepted'`) — those are pre-engagement
 * "contacts ready for outreach". Engaged ones live in /leads.
 *
 * v3 stage semantics:
 *   stage 1 = scoperto via Google Places (L1)
 *   stage 2 = arricchito via scraping web (L2)
 *   stage 3 = filtrato qualità edificio (L3)
 *   stage 4 = qualificato Solar API (L4)
 *   stage 5 = scored da Haiku (L5)
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';
import { isModeratedTenant } from '@/lib/data/tenant';

// Types + display-value resolvers live in a non-server module so client
// components (e.g. contatti-table.tsx) can import them safely. We
// re-export here for backward compat with existing imports.
export {
  displayCity,
  displayEmail,
  displayName,
  displayOverallScore,
  displayPhone,
  displayProvince,
  displayWebsite,
  type ContactExtraction,
  type ContattoRow,
  type PlacesEnrichment,
  type ProxyScoreData,
  type SolarVerdict,
} from '@/lib/contatti-display';
import type { ContattoRow, SolarVerdict } from '@/lib/contatti-display';

export const CONTATTI_PAGE_SIZE = 50;

export interface ContattoListResult {
  rows: ContattoRow[];
  total: number;
}

export interface ContattiSummary {
  l1: number;
  l1_30d: number;          // L1 candidates created in the last 30 days —
                           // used by the Panoramica KPI chip so the
                           // "Scansionati" number aligns with the other
                           // 30d-windowed KPIs next to it.
  l2: number;
  l3: number;
  l4_qualified: number;    // solar_verdict = 'accepted'
  l4_rejected: number;     // solar_verdict = 'rejected_tech'
  l4_no_building: number;
  l4_skipped: number;      // solar_verdict = 'skipped_below_gate'
  total: number;           // = l1 (all stage >= 1)
  // ─── v3 metrics — qualitative aggregates over `accepted` rows ───
  // These power the redesigned KPI strip on /contatti. Computed
  // in-memory after a single SELECT on the qualified candidates,
  // so cost is bounded by `l4_qualified` (typically <500/tenant).
  total_kwp_installable: number;     // SUM(solar_kw_installable) on accepted
  avg_overall_score: number | null;  // AVG(proxy_score_data.overall_score)
  valid_email_count: number;         // count with best_email AND not flagged
                                     // disposable/free_email_provider_b2b
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
  score, stage, solar_verdict, created_at, roof_id,
  predicted_sector, building_quality_score,
  proxy_score_data, scraped_data, contact_extraction, enrichment,
  territories:territory_id(name, type, code)
`.trim();

export interface ContattiFilter {
  stage?: number;
  territory_id?: string;
  solar_verdict?: SolarVerdict;
  /**
   * When true, returns ALL scan_candidates (also rejected / mid-funnel /
   * not yet promoted to a lead). Used by the operator-facing "mostra
   * anche scartati" toggle on /contatti — never the default.
   */
  include_unpromoted?: boolean;
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
  if (opts.filter?.solar_verdict) {
    q = q.eq('solar_verdict', opts.filter.solar_verdict);
  } else if (!opts.filter?.include_unpromoted) {
    // Default: only candidates that passed Solar API. The promoted-to-lead
    // filter happens after the resolve step below.
    q = q.eq('solar_verdict', 'accepted');
  }

  const { data, error, count } = await q
    .order('created_at', { ascending: false })
    .range(from, to);

  if (error) throw new Error(`listContatti: ${error.message}`);

  let rows = (data ?? []) as unknown as ContattoRow[];

  // Resolve lead_id per contact by the *candidate identity*, NOT by
  // roof_id. A roof can host more than one business, but `subjects` is
  // UNIQUE (tenant_id, roof_id) — so a roof_id→lead map would point
  // every co-located contact at the same (single) lead, and clicking
  // one contact would open another. L6 stamps the originating
  // candidate id on the subject (`raw_data.scan_candidate_id`); we map
  // each contact through that to its own lead.
  const roofIds = rows
    .map((r) => r.roof_id)
    .filter((v): v is string => typeof v === 'string');
  const candToLead = new Map<string, string>();
  if (roofIds.length > 0) {
    const { data: leadRows } = await sb
      .from('leads')
      .select('id, subjects(raw_data)')
      .in('roof_id', roofIds);
    type SubjectJoin = { raw_data: Record<string, unknown> | null };
    type LeadJoin = {
      id: string;
      subjects: SubjectJoin | SubjectJoin[] | null;
    };
    for (const l of (leadRows ?? []) as unknown as LeadJoin[]) {
      // PostgREST embeds a to-one relation as an object, but the
      // generated types widen it to an array — handle both.
      const subj = Array.isArray(l.subjects) ? l.subjects[0] : l.subjects;
      const candId = subj?.raw_data?.['scan_candidate_id'];
      if (typeof candId === 'string') candToLead.set(candId, l.id);
    }
  }
  for (const r of rows) {
    (r as ContattoRow & { lead_id?: string | null }).lead_id =
      candToLead.get(r.id) ?? null;
  }

  // Default view: only rows that have been promoted to a lead are shown
  // (perfect contacts). Scartati pre-L6 stay invisible unless the operator
  // flips include_unpromoted.
  if (!opts.filter?.include_unpromoted) {
    rows = rows.filter(
      (r) =>
        (r as ContattoRow & { lead_id?: string | null }).lead_id != null,
    );
  }

  // The header `count` is the SQL row count *before* the lead-id post-filter,
  // so we override with the post-filter length to keep the page chip consistent.
  const totalShown = opts.filter?.include_unpromoted ? (count ?? 0) : rows.length;
  return { rows, total: totalShown };
}

/** Stage counts + v3 quality aggregates for the header summary strip.
 *
 * The "convalidati" KPI counts only candidates that passed Solar API
 * AND have been promoted to a `leads` row (the same rows the table
 * shows by default). This keeps KPI ↔ table strictly coherent.
 */
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

  const since30d = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();
  const countGteSince = async (
    minStage: number,
    sinceIso: string,
  ): Promise<number> => {
    const { count, error } = await sb
      .from('scan_candidates')
      .select('id', { count: 'exact', head: true })
      .gte('stage', minStage)
      .gte('created_at', sinceIso);
    if (error) return 0;
    return count ?? 0;
  };

  // KPI counts must mirror the table filter (`solar_verdict=verdict`
  // alone — no stage gate). The stage column lags the verdict in some
  // pipelines (a candidate may carry verdict='accepted' at stage=1 if
  // the Solar gate ran out-of-order), and gating the KPI on stage>=4
  // produced "table shows 9 rows / KPI says 0" inconsistencies.
  const countVerdict = async (verdict: SolarVerdict): Promise<number> => {
    const { count, error } = await sb
      .from('scan_candidates')
      .select('id', { count: 'exact', head: true })
      .eq('solar_verdict', verdict);
    if (error) return 0;
    return count ?? 0;
  };

  // Compute the set of accepted candidate roofs that have been promoted
  // to a lead. Both KPI counts and aggregates filter by this set so the
  // strip never claims "0 convalidati" while the table renders rows.
  const fetchPromotedAcceptedRows = async (): Promise<
    Array<{
      solar_kw_installable: number | null;
      proxy_score_data: Record<string, unknown> | null;
      contact_extraction: Record<string, unknown> | null;
    }>
  > => {
    const { data: leadRoofs } = await sb
      .from('leads')
      .select('roof_id')
      .not('roof_id', 'is', null);
    const promotedRoofIds = (leadRoofs ?? [])
      .map((l) => (l as { roof_id: string | null }).roof_id)
      .filter((v): v is string => typeof v === 'string');
    if (promotedRoofIds.length === 0) return [];

    // Mirror the table filter: solar_verdict='accepted' is sufficient,
    // stage is intentionally unconstrained (see countVerdict above).
    const { data, error } = await sb
      .from('scan_candidates')
      .select('solar_kw_installable, proxy_score_data, contact_extraction')
      .eq('solar_verdict', 'accepted')
      .in('roof_id', promotedRoofIds);
    if (error || !data) return [];
    return data as Array<{
      solar_kw_installable: number | null;
      proxy_score_data: Record<string, unknown> | null;
      contact_extraction: Record<string, unknown> | null;
    }>;
  };

  // Single-pass aggregate over the promoted accepted rows — feeds the
  // KPI strip (kWp totali, score AI medio, email valida).
  const fetchQualifiedAggregates = async (): Promise<{
    total_kwp_installable: number;
    avg_overall_score: number | null;
    valid_email_count: number;
    convalidati_count: number;
  }> => {
    const data = await fetchPromotedAcceptedRows();
    if (data.length === 0) {
      return {
        total_kwp_installable: 0,
        avg_overall_score: null,
        valid_email_count: 0,
        convalidati_count: 0,
      };
    }

    let kwpSum = 0;
    let scoreSum = 0;
    let scoreCount = 0;
    let validEmail = 0;

    for (const r of data as Array<{
      solar_kw_installable: number | null;
      proxy_score_data: Record<string, unknown> | null;
      contact_extraction: Record<string, unknown> | null;
    }>) {
      // kWp totali
      if (typeof r.solar_kw_installable === 'number') {
        kwpSum += r.solar_kw_installable;
      }
      // Score AI medio
      const overall = r.proxy_score_data?.overall_score;
      if (typeof overall === 'number') {
        scoreSum += overall;
        scoreCount += 1;
      }
      // Email valida = best_email presente AND no flag disposable/free.
      // Allineato con il validatore anti-spam (services/lead_quality_validator.py):
      // tutti gli account "consumer" (gmail/yahoo/libero/...) sono
      // esclusi perché non rappresentano un vero contatto B2B.
      const email = r.contact_extraction?.best_email;
      if (typeof email === 'string' && email) {
        const flags = Array.isArray(r.proxy_score_data?.flags)
          ? (r.proxy_score_data!.flags as string[])
          : [];
        if (
          !flags.includes('disposable_email') &&
          !flags.includes('free_email_provider_b2b')
        ) {
          validEmail += 1;
        }
      }
    }

    return {
      total_kwp_installable: Math.round(kwpSum),
      avg_overall_score: scoreCount > 0 ? Math.round(scoreSum / scoreCount) : null,
      valid_email_count: validEmail,
      convalidati_count: data.length,
    };
  };

  const [
    l1,
    l1_30d,
    l2,
    l3,
    l4_rejected,
    l4_no_building,
    l4_skipped,
    aggregates,
  ] = await Promise.all([
    countGte(1),
    countGteSince(1, since30d),
    countGte(2),
    countGte(3),
    countVerdict('rejected_tech'),
    countVerdict('no_building'),
    countVerdict('skipped_below_gate'),
    fetchQualifiedAggregates(),
  ]);

  // KPI "convalidati" = candidates that passed Solar API AND were
  // promoted to a lead. Same set the table shows by default. The raw
  // `_l4_qualified_raw` count (still computed for the chip strip) only
  // matters when the operator flips the "scartati" toggle on.
  const { convalidati_count, ...kpiAggregates } = aggregates;

  return {
    l1,
    l1_30d,
    l2,
    l3,
    l4_qualified: convalidati_count,
    l4_rejected,
    l4_no_building,
    l4_skipped,
    total: l1,
    ...kpiAggregates,
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

  // Moderation freeze: on a moderated tenant the ENGAGEMENT rows of the
  // funnel (Aperti/Cliccati/Engaged/Appuntamento/Firmati) must only count
  // PROMOTED contatti — otherwise the absolute counts + conversion rates
  // leak who engaged before promotion. Sent/Delivered stay unfiltered
  // (operator-driven, no reaction).
  const moderated = await isModeratedTenant();

  const countLead = async (
    col?: string,
    val?: string,
    freeze = false,
  ): Promise<number> => {
    let q = sb.from('leads').select('id', { count: 'exact', head: true });
    if (col && val) q = q.eq(col, val);
    if (moderated && freeze) q = q.not('operator_released_at', 'is', null);
    const { count, error } = await q;
    if (error) return 0;
    return count ?? 0;
  };

  const countLeadNotNull = async (col: string, freeze = false): Promise<number> => {
    let q = sb
      .from('leads')
      .select('id', { count: 'exact', head: true })
      .not(col, 'is', null);
    if (moderated && freeze) q = q.not('operator_released_at', 'is', null);
    const { count, error } = await q;
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
    // L4 verdict counts must use gteStage: 4 — once L5 scoring runs an
    // accepted candidate is advanced to stage=5, so a strict `stage=4`
    // filter would drop everything that completed the funnel.
    countSc({ gteStage: 4, solar_verdict: 'accepted' }),
    countSc({ gteStage: 4, solar_verdict: 'rejected_tech' }),
    countSc({ gteStage: 4, solar_verdict: 'skipped_below_gate' }),
    countLead(),
    countLeadNotNull('outreach_sent_at'),
    countLeadNotNull('outreach_delivered_at'),
    countLeadNotNull('outreach_opened_at', true),
    countLeadNotNull('outreach_clicked_at', true),
    countLead('pipeline_status', 'engaged', true),
    countLead('pipeline_status', 'appointment', true),
    countLead('pipeline_status', 'closed_won', true),
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

/** Count of qualified contacts (`solar_verdict='accepted'`). Used by the
 *  /leads page to show a "X contatti in attesa di reazione" call-out
 *  when the engaged-leads list is empty. */
export async function getQualifiedContattiCount(): Promise<number> {
  const sb = await createSupabaseServerClient();
  const { count, error } = await sb
    .from('scan_candidates')
    .select('id', { count: 'exact', head: true })
    .eq('solar_verdict', 'accepted');
  if (error) return 0;
  return count ?? 0;
}

// Display-value resolvers and types live in `lib/contatti-display.ts`
// and are re-exported at the top of this file (so client components
// can import them without dragging the Supabase server bundle in).
