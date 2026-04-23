/**
 * Geo & analytics data functions for the premium dashboard widgets.
 *
 * All functions run server-side (RLS-scoped Supabase client).
 * No new API endpoints needed — we query Supabase directly from
 * Next.js Server Components.
 *
 * Functions:
 *  - getGeoLeads()        → pins + province aggregates for GeoRadarMap
 *  - getSendTimeHeatmap() → open-rate by DOW × hour for SmartTimeHeatmap
 *  - getPipelineRevenue() → revenue estimate by funnel stage
 *  - getAiInsights()      → rule-based actionable insights for AiExecutiveInsights
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';
import type { LeadScoreTier, LeadStatus } from '@/types/db';

// ── shared types ──────────────────────────────────────────────────────────────

export interface GeoLeadPin {
  id: string;
  provincia: string; // 2-char province code
  comune: string | null;
  pipeline_status: LeadStatus;
  score_tier: LeadScoreTier;
  score: number;
  outreach_opened_at: string | null;
  outreach_clicked_at: string | null;
  created_at: string;
}

export interface ProvinceAggregate {
  provincia: string;
  total: number;
  hot: number;
  appointments: number;
  won: number;
}

export interface HeatmapCell {
  dow: number; // 0=Sun … 6=Sat
  hour: number; // 0-23
  opens: number;
  normalized: number; // 0-1
}

export interface PipelineStageRevenue {
  label: string;
  status: string;
  count: number;
  estimated_eur: number;
  color: string;
}

export interface AiInsight {
  type: 'warning' | 'opportunity' | 'info' | 'success';
  title: string;
  body: string;
  action_href?: string;
  action_label?: string;
  metric?: string;
}

// ── internal helper ───────────────────────────────────────────────────────────

/** Convert an ISO timestamp to {dow, hour} in Europe/Rome timezone. */
function toRomeParts(isoString: string): { dow: number; hour: number } {
  const d = new Date(isoString);
  // Use en-US locale parts so we get stable English weekday names
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Europe/Rome',
    hour: 'numeric',
    hour12: false,
    weekday: 'short',
  }).formatToParts(d);

  const hourStr = parts.find((p) => p.type === 'hour')?.value ?? '0';
  const weekday = parts.find((p) => p.type === 'weekday')?.value ?? 'Sun';
  const WEEKDAYS: Record<string, number> = {
    Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6,
  };
  return {
    hour: Math.min(23, parseInt(hourStr, 10)),
    dow: WEEKDAYS[weekday] ?? 0,
  };
}

// ── geo leads ─────────────────────────────────────────────────────────────────

/**
 * Returns up to 500 top-scored leads with their provincia,
 * plus a pre-aggregated per-province summary.
 */
export async function getGeoLeads(): Promise<{
  pins: GeoLeadPin[];
  aggregates: ProvinceAggregate[];
}> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('leads')
    .select(
      `id, pipeline_status, score_tier, score,
       outreach_opened_at, outreach_clicked_at, created_at,
       roofs:roofs(provincia, comune)`,
    )
    .not('pipeline_status', 'in', '("blacklisted")')
    .order('score', { ascending: false })
    .limit(500);

  if (error) throw new Error(`getGeoLeads: ${error.message}`);

  type RawRow = {
    id: string;
    pipeline_status: LeadStatus;
    score_tier: LeadScoreTier;
    score: number;
    outreach_opened_at: string | null;
    outreach_clicked_at: string | null;
    created_at: string;
    roofs: { provincia: string | null; comune: string | null } | null;
  };

  const rows = (data ?? []) as unknown as RawRow[];

  const pins: GeoLeadPin[] = rows
    .filter((r) => r.roofs?.provincia)
    .map((r) => ({
      id: r.id,
      provincia: r.roofs!.provincia!.toUpperCase().trim(),
      comune: r.roofs?.comune ?? null,
      pipeline_status: r.pipeline_status,
      score_tier: r.score_tier,
      score: r.score,
      outreach_opened_at: r.outreach_opened_at,
      outreach_clicked_at: r.outreach_clicked_at,
      created_at: r.created_at,
    }));

  // Province-level aggregation
  const aggMap = new Map<string, ProvinceAggregate>();
  for (const pin of pins) {
    const p = pin.provincia;
    if (!aggMap.has(p)) {
      aggMap.set(p, { provincia: p, total: 0, hot: 0, appointments: 0, won: 0 });
    }
    const agg = aggMap.get(p)!;
    agg.total++;
    if (pin.score_tier === 'hot') agg.hot++;
    if (pin.pipeline_status === 'appointment') agg.appointments++;
    if (pin.pipeline_status === 'closed_won') agg.won++;
  }

  return { pins, aggregates: Array.from(aggMap.values()) };
}

// ── send-time heatmap ─────────────────────────────────────────────────────────

/**
 * Returns a 7×24 matrix of email-open counts (indexed by DOW and hour,
 * Rome timezone), plus a normalized 0-1 value for CSS intensity mapping.
 */
export async function getSendTimeHeatmap(days = 90): Promise<HeatmapCell[]> {
  const supabase = await createSupabaseServerClient();
  const since = new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();

  const { data, error } = await supabase
    .from('leads')
    .select('outreach_opened_at')
    .not('outreach_opened_at', 'is', null)
    .gte('outreach_opened_at', since);

  if (error) throw new Error(`getSendTimeHeatmap: ${error.message}`);

  // Flat map keyed by `dow * 24 + hour` to avoid noUncheckedIndexedAccess issues
  const flat = new Map<number, number>();
  let maxVal = 0;

  for (const row of data ?? []) {
    if (!row.outreach_opened_at) continue;
    const { dow, hour } = toRomeParts(row.outreach_opened_at);
    const k = dow * 24 + hour;
    const prev = flat.get(k) ?? 0;
    const next = prev + 1;
    flat.set(k, next);
    if (next > maxVal) maxVal = next;
  }

  const cells: HeatmapCell[] = [];
  for (let dow = 0; dow < 7; dow++) {
    for (let hour = 0; hour < 24; hour++) {
      const opens = flat.get(dow * 24 + hour) ?? 0;
      cells.push({
        dow,
        hour,
        opens,
        normalized: maxVal > 0 ? opens / maxVal : 0,
      });
    }
  }
  return cells;
}

// ── pipeline revenue ──────────────────────────────────────────────────────────

/**
 * Estimates pipeline revenue by funnel stage.
 *
 * Formula: Σ(lead.roi_data.estimated_kwp ?? 8) × €1 500/kWp × stage_conversion_factor
 *
 * The €1 500/kWp is a conservative Italian market reference rate. The
 * conversion factors reflect average close rates for each stage:
 *   sent/new    5%, opened/clicked  15%, whatsapp  35%,
 *   appointment 55%, won           100%.
 */
export async function getPipelineRevenue(): Promise<PipelineStageRevenue[]> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('leads')
    .select('pipeline_status, roi_data')
    .not('pipeline_status', 'in', '("blacklisted","closed_lost")');

  if (error) throw new Error(`getPipelineRevenue: ${error.message}`);

  type Bucket = {
    label: string;
    statuses: string[];
    conversion: number;
    color: string;
    order: number;
  };

  const BUCKETS: Record<string, Bucket> = {
    top: {
      label: 'Inviati', statuses: ['new', 'sent', 'delivered'],
      conversion: 0.05, color: '#1a73e8', order: 0,
    },
    opened: {
      label: 'Aperti', statuses: ['opened', 'clicked', 'engaged'],
      conversion: 0.15, color: '#fdbb31', order: 1,
    },
    whatsapp: {
      label: 'Risposta', statuses: ['whatsapp'],
      conversion: 0.35, color: '#00c853', order: 2,
    },
    appointment: {
      label: 'Appuntamento', statuses: ['appointment'],
      conversion: 0.55, color: '#ff8c00', order: 3,
    },
    won: {
      label: 'Firmati', statuses: ['closed_won'],
      conversion: 1.0, color: '#006a37', order: 4,
    },
  };

  // Use a Map to avoid noUncheckedIndexedAccess issues with Record indexing
  const totals = new Map<string, { count: number; kwp: number }>();
  for (const k of Object.keys(BUCKETS)) totals.set(k, { count: 0, kwp: 0 });

  for (const row of data ?? []) {
    const status = row.pipeline_status as string;
    const kwp = ((row.roi_data as { estimated_kwp?: number } | null)?.estimated_kwp) ?? 8;
    for (const [key, bucket] of Object.entries(BUCKETS)) {
      if (bucket.statuses.includes(status)) {
        const entry = totals.get(key);
        if (entry) {
          entry.count++;
          entry.kwp += kwp;
        }
        break;
      }
    }
  }

  return Object.entries(BUCKETS)
    .sort(([, a], [, b]) => a.order - b.order)
    .map(([key, bucket]) => {
      const entry = totals.get(key) ?? { count: 0, kwp: 0 };
      return {
        label: bucket.label,
        status: key,
        count: entry.count,
        estimated_eur: Math.round(entry.kwp * 1500 * bucket.conversion),
        color: bucket.color,
      };
    });
}

// ── ai insights ───────────────────────────────────────────────────────────────

/**
 * Rule-based insights — no LLM call, purely derived from Supabase queries.
 * Returns 3–5 prioritised insights for the AI Executive Insights widget.
 */
export async function getAiInsights(): Promise<AiInsight[]> {
  const supabase = await createSupabaseServerClient();
  const now = new Date();
  const h48 = new Date(now.getTime() - 48 * 3600 * 1000).toISOString();
  const h24 = new Date(now.getTime() - 24 * 3600 * 1000).toISOString();
  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1).toISOString();
  const prevMonthStart = new Date(now.getFullYear(), now.getMonth() - 1, 1).toISOString();

  const [
    staleHotRes,
    freshOpensRes,
    appointmentsRes,
    warmUncontactedRes,
    wonThisMonthRes,
    wonPrevMonthRes,
  ] = await Promise.all([
    // Hot leads that opened but stalled for 48h+
    supabase
      .from('leads')
      .select('id', { count: 'exact', head: true })
      .eq('score_tier', 'hot')
      .eq('pipeline_status', 'opened')
      .lte('outreach_opened_at', h48),

    // Leads that opened in the last 24h (fresh opportunity)
    supabase
      .from('leads')
      .select('id', { count: 'exact', head: true })
      .in('pipeline_status', ['opened', 'clicked'])
      .gte('outreach_opened_at', h24),

    // Active appointments
    supabase
      .from('leads')
      .select('id', { count: 'exact', head: true })
      .eq('pipeline_status', 'appointment'),

    // Warm leads not yet contacted (no outreach sent)
    supabase
      .from('leads')
      .select('id', { count: 'exact', head: true })
      .eq('score_tier', 'warm')
      .is('outreach_sent_at', null),

    // Won this month (with roi_data for value estimate)
    supabase
      .from('leads')
      .select('id, roi_data')
      .eq('pipeline_status', 'closed_won')
      .gte('updated_at', monthStart),

    // Won last month (comparison)
    supabase
      .from('leads')
      .select('id', { count: 'exact', head: true })
      .eq('pipeline_status', 'closed_won')
      .gte('updated_at', prevMonthStart)
      .lt('updated_at', monthStart),
  ]);

  const insights: AiInsight[] = [];

  // 1. Stale hot leads
  const staleHot = staleHotRes.count ?? 0;
  if (staleHot > 0) {
    insights.push({
      type: 'warning',
      title: `${staleHot} lead hot senza follow-up`,
      body: `${staleHot > 1 ? `${staleHot} lead hot hanno` : 'Un lead hot ha'} aperto la tua email oltre 48 ore fa senza ricevere un contatto diretto.`,
      action_href: '/leads?tier=hot&status=opened',
      action_label: 'Contatta ora',
      metric: String(staleHot),
    });
  }

  // 2. Fresh opens (last 24h)
  const freshOpens = freshOpensRes.count ?? 0;
  if (freshOpens > 0) {
    insights.push({
      type: 'opportunity',
      title: `${freshOpens} apertur${freshOpens === 1 ? 'a' : 'e'} nelle ultime 24h`,
      body: `Momento ideale per una chiamata — ${freshOpens > 1 ? `${freshOpens} lead hanno` : 'un lead ha'} aperto la tua email di recente.`,
      action_href: '/leads?status=opened',
      action_label: 'Chiama adesso',
      metric: `+${freshOpens}`,
    });
  }

  // 3. Appointments
  const appointments = appointmentsRes.count ?? 0;
  if (appointments > 0) {
    insights.push({
      type: 'info',
      title: `${appointments} appuntament${appointments === 1 ? 'o' : 'i'} programmati`,
      body: `${appointments > 1 ? `${appointments} lead sono` : 'Un lead è'} in fase appuntamento — verifica che il rendering sia pronto.`,
      action_href: '/leads?status=appointment',
      action_label: 'Prepara sopralluogo',
      metric: String(appointments),
    });
  }

  // 4. Warm leads not yet contacted
  const warmUncontacted = warmUncontactedRes.count ?? 0;
  if (warmUncontacted > 0) {
    insights.push({
      type: 'opportunity',
      title: `${warmUncontacted} lead warm da contattare`,
      body: `Hai ${warmUncontacted} lead con buon punteggio che non hanno ancora ricevuto un'email outreach.`,
      action_href: '/leads?tier=warm',
      action_label: 'Avvia outreach',
      metric: String(warmUncontacted),
    });
  }

  // 5. Won this month vs last
  const wonRows = wonThisMonthRes.data ?? [];
  const wonCount = wonRows.length;
  if (wonCount > 0) {
    const wonValue = wonRows.reduce((acc, r) => {
      const kwp = ((r.roi_data as { estimated_kwp?: number } | null)?.estimated_kwp) ?? 8;
      return acc + kwp * 1500;
    }, 0);
    const prevWon = wonPrevMonthRes.count ?? 0;
    const delta = prevWon > 0 ? Math.round(((wonCount - prevWon) / prevWon) * 100) : null;
    insights.push({
      type: 'success',
      title: `${wonCount} contratt${wonCount === 1 ? 'o' : 'i'} firmati questo mese`,
      body: `Valore stimato €${Math.round(wonValue).toLocaleString('it-IT')}${
        delta !== null
          ? `. ${delta >= 0 ? `+${delta}%` : `${delta}%`} rispetto al mese scorso.`
          : '.'
      }`,
      action_href: '/leads?status=closed_won',
      action_label: 'Vedi chiusure',
      metric: `€${wonValue >= 1000 ? `${(wonValue / 1000).toFixed(0)}k` : Math.round(wonValue)}`,
    });
  }

  return insights;
}
