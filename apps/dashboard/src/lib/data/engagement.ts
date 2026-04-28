/**
 * Portal engagement — read-side accessors for the dashboard.
 *
 * Two flavours:
 *
 *  - **Denormalised snapshot** (``leads.engagement_score`` et al.) is
 *    refreshed nightly by ``engagement_rollup_cron`` in the API. The
 *    list view and lead detail page read it directly from ``LeadListRow``
 *    — no extra query needed.
 *
 *  - **Right-now signal** (``getHotLeadsNow``) re-queries
 *    ``portal_events`` for the last N minutes so the dashboard can show
 *    "X has opened the dossier 5 times in the last 10 minutes" without
 *    waiting for the nightly rollup. This is the real-time companion
 *    the user explicitly asked for.
 *
 * Everything is RLS-scoped via ``auth_tenant_id()`` in the SELECT
 * policy on ``portal_events``, so we never pass ``tenant_id``.
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';

export interface HotLeadNow {
  lead_id: string;
  recent_events: number;
  last_event_at: string;
  public_slug: string | null;
  display_name: string | null;
  engagement_score: number;
}

/**
 * Leads with the most portal_events in the last ``minutes`` minutes.
 *
 * Intended for the dashboard "caldi adesso" widget — returns up to
 * ``limit`` rows sorted by recent_events DESC. When no one has been
 * active in the window, returns an empty array (the UI renders an
 * idle-state card).
 *
 * Implementation note: we fetch raw events and aggregate in JS rather
 * than pushing a group_by through PostgREST. The window is
 * ``minutes`` minutes wide (≤120 in practice) and the events per tab
 * are bounded by the beacon rate-limiter to ≤60/min, so even a
 * high-traffic tenant is pulling a few hundred rows at most. Worth
 * the simpler query.
 */
export async function getHotLeadsNow(
  options: { minutes?: number; limit?: number } = {},
): Promise<HotLeadNow[]> {
  const minutes = options.minutes ?? 60;
  const limit = options.limit ?? 10;
  const sb = await createSupabaseServerClient();
  const since = new Date(Date.now() - minutes * 60_000).toISOString();

  // Step 1: grab event rows — RLS scopes them to the tenant.
  const { data: eventRows, error: eventsErr } = await sb
    .from('portal_events')
    .select('lead_id, occurred_at')
    .gte('occurred_at', since);
  if (eventsErr) {
    throw new Error(`getHotLeadsNow(events): ${eventsErr.message}`);
  }
  if (!eventRows || eventRows.length === 0) return [];

  type Agg = { count: number; last: string };
  const agg = new Map<string, Agg>();
  for (const row of eventRows as Array<{ lead_id: string; occurred_at: string }>) {
    const prev = agg.get(row.lead_id);
    if (prev) {
      prev.count += 1;
      if (row.occurred_at > prev.last) prev.last = row.occurred_at;
    } else {
      agg.set(row.lead_id, { count: 1, last: row.occurred_at });
    }
  }

  const ranked = [...agg.entries()]
    .sort((a, b) => b[1].count - a[1].count)
    .slice(0, limit);
  const topIds = ranked.map(([id]) => id);

  // Step 2: resolve slugs + display labels for the top N. One batched
  // IN query — still RLS-scoped.
  const { data: leadRows, error: leadsErr } = await sb
    .from('leads')
    .select(
      'id, public_slug, engagement_score, ' +
        'subjects:subjects(business_name, owner_first_name, owner_last_name)',
    )
    .in('id', topIds);
  if (leadsErr) {
    throw new Error(`getHotLeadsNow(leads): ${leadsErr.message}`);
  }

  type LeadMini = {
    id: string;
    public_slug: string | null;
    engagement_score: number | null;
    subjects: {
      business_name: string | null;
      owner_first_name: string | null;
      owner_last_name: string | null;
    } | null;
  };
  const leadMap = new Map<string, LeadMini>();
  for (const lead of (leadRows ?? []) as unknown as LeadMini[]) {
    leadMap.set(lead.id, lead);
  }

  return ranked.map(([leadId, a]) => {
    const lead = leadMap.get(leadId);
    const subj = lead?.subjects;
    const display =
      subj?.business_name?.trim() ||
      [subj?.owner_first_name, subj?.owner_last_name]
        .filter(Boolean)
        .join(' ')
        .trim() ||
      null;
    return {
      lead_id: leadId,
      recent_events: a.count,
      last_event_at: a.last,
      public_slug: lead?.public_slug ?? null,
      display_name: display || null,
      engagement_score: lead?.engagement_score ?? 0,
    };
  });
}

/**
 * Human-readable tier for an engagement score. Keep the thresholds in
 * sync with ``compute_score`` in ``engagement_service.py`` — cold
 * covers the silent majority, warm is "showing interest", hot is
 * "call today".
 */
export function engagementTier(score: number): 'hot' | 'warm' | 'cold' {
  if (score >= 60) return 'hot';
  if (score >= 25) return 'warm';
  return 'cold';
}

// ---------------------------------------------------------------------
// Portal events — full activity log for one lead, used by the lead
// detail page "Attività portale" section. Distinct from the email
// `events` table read by listEventsForLead.
// ---------------------------------------------------------------------

export interface PortalEventRow {
  id: number;
  event_kind: string;
  metadata: Record<string, unknown> | null;
  elapsed_ms: number | null;
  occurred_at: string;
  session_id: string;
}

/**
 * Most-recent portal events for a single lead. Capped at ``limit`` rows
 * to keep the timeline UI bounded — the operator wants the last
 * heartbeat, not three months of history.
 */
export async function listPortalEventsForLead(
  leadId: string,
  limit = 50,
): Promise<PortalEventRow[]> {
  const sb = await createSupabaseServerClient();
  const { data, error } = await sb
    .from('portal_events')
    .select('id, event_kind, metadata, elapsed_ms, occurred_at, session_id')
    .eq('lead_id', leadId)
    .order('occurred_at', { ascending: false })
    .limit(limit);
  if (error) throw new Error(`listPortalEventsForLead: ${error.message}`);
  return (data ?? []) as PortalEventRow[];
}
