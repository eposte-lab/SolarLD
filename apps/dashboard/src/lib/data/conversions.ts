/**
 * Conversion attribution — read-side accessors for the dashboard.
 *
 * Part B.6: closed-loop attribution. The ``conversions`` table has one
 * row per (lead, stage); the service-role API writes via the public
 * pixel / POST endpoints; the dashboard reads via RLS-scoped SELECT.
 *
 * The ``amount_cents`` column is nullable — the pixel endpoint can't
 * carry a value. Operators use the POST endpoint (or Zapier) to
 * attach deal amounts after the fact.
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';
import type { ConversionRow, ConversionStats } from '@/types/db';

const COLUMNS = 'id, tenant_id, lead_id, stage, amount_cents, source, closed_at, created_at';

/**
 * Aggregate conversion counts + pipeline value for the overview card.
 *
 * Deliberately in-process rather than a Postgres function: the data
 * set is tiny (one row per won lead) and keeping the aggregation in
 * TypeScript avoids an extra migration.
 *
 * @param days  Rolling window for ``closed_at`` filter (default 30).
 */
export async function getConversionStats(days = 30): Promise<ConversionStats> {
  const sb = await createSupabaseServerClient();
  const since = new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();

  const { data, error } = await sb
    .from('conversions')
    .select('stage, amount_cents')
    .gte('closed_at', since);

  if (error) throw new Error(`getConversionStats: ${error.message}`);

  const stats: ConversionStats = {
    booked: 0,
    quoted: 0,
    won: 0,
    lost: 0,
    won_value_cents: 0,
  };

  for (const row of data ?? []) {
    if (row.stage === 'booked') stats.booked++;
    else if (row.stage === 'quoted') stats.quoted++;
    else if (row.stage === 'won') {
      stats.won++;
      stats.won_value_cents += row.amount_cents ?? 0;
    } else if (row.stage === 'lost') stats.lost++;
  }

  return stats;
}

/**
 * Most recent N conversion rows for a single lead — used on the
 * lead detail page to show the conversion history.
 */
export async function getConversionsForLead(
  leadId: string,
): Promise<ConversionRow[]> {
  const sb = await createSupabaseServerClient();
  const { data, error } = await sb
    .from('conversions')
    .select(COLUMNS)
    .eq('lead_id', leadId)
    .order('closed_at', { ascending: true });

  if (error) throw new Error(`getConversionsForLead: ${error.message}`);
  return (data ?? []) as unknown as ConversionRow[];
}
