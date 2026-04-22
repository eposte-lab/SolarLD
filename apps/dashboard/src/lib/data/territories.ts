/**
 * Territory data access — server-only, RLS-scoped.
 *
 * `territories_all` RLS policy (migration 0011) restricts every
 * SELECT/INSERT/UPDATE/DELETE to `tenant_id = auth_tenant_id()`, so
 * we never pass `tenant_id` explicitly from the dashboard. The
 * tenant binding is derived server-side from the authenticated
 * session cookie.
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';
import type { TerritoryRow } from '@/types/db';

const LIST_COLUMNS =
  'id, tenant_id, type, code, name, bbox, excluded, priority, created_at, updated_at';

// ---------------------------------------------------------------------------
// Scan summary per territory
// ---------------------------------------------------------------------------

export interface ScanSummary {
  territory_id: string;
  occurred_at: string;
  leads_qualified: number;
  total_cost_cents: number;
  /** True when Atoka returned 0 candidates at L1 (config / key issue). */
  atoka_empty: boolean;
}

/**
 * Return the most-recent `scan.completed` event for each territory.
 *
 * The orchestrator embeds `territory_id` in both `scan.completed` and
 * `scan.l1_complete` payloads.  We fetch the last 400 events of both
 * types and group them by scan_id — one round-trip, no extra RPC.
 */
export async function listScanSummaries(
  territoryIds: string[],
): Promise<Map<string, ScanSummary>> {
  if (!territoryIds.length) return new Map();

  const sb = await createSupabaseServerClient();
  const { data, error } = await sb
    .from('events')
    .select('event_type, payload, occurred_at')
    .in('event_type', ['scan.completed', 'scan.l1_complete'])
    .order('occurred_at', { ascending: false })
    .limit(400);

  if (error || !data) return new Map();

  // Group rows by scan_id so we can join completed + l1 data
  type Entry = {
    completed?: Record<string, unknown>;
    l1?: Record<string, unknown>;
    occurred_at?: string;
  };
  const byScan = new Map<string, Entry>();

  for (const row of data) {
    const p = row.payload as Record<string, unknown>;
    const sid = p?.scan_id as string | undefined;
    if (!sid) continue;
    const entry: Entry = byScan.get(sid) ?? {};
    if (row.event_type === 'scan.completed') {
      entry.completed = p;
      entry.occurred_at = row.occurred_at as string;
    } else {
      entry.l1 = p;
    }
    byScan.set(sid, entry);
  }

  // Pick the newest completed scan per territory (data already desc order)
  const result = new Map<string, ScanSummary>();
  for (const entry of byScan.values()) {
    if (!entry.completed || !entry.occurred_at) continue;
    const tid = (entry.completed.territory_id ??
      entry.l1?.territory_id) as string | undefined;
    if (!tid || !territoryIds.includes(tid)) continue;
    if (result.has(tid)) continue; // already have newer

    result.set(tid, {
      territory_id: tid,
      occurred_at: entry.occurred_at,
      leads_qualified: Number(entry.completed.leads_qualified ?? 0),
      total_cost_cents: Number(entry.completed.total_cost_cents ?? 0),
      atoka_empty: Number(entry.l1?.candidates ?? -1) === 0,
    });
  }
  return result;
}

// ---------------------------------------------------------------------------
// Territory list
// ---------------------------------------------------------------------------

/** All territories for the current tenant, newest first. */
export async function listTerritories(): Promise<TerritoryRow[]> {
  const sb = await createSupabaseServerClient();
  const { data, error } = await sb
    .from('territories')
    .select(LIST_COLUMNS)
    .order('created_at', { ascending: false });
  if (error) throw new Error(`listTerritories: ${error.message}`);
  return (data ?? []) as unknown as TerritoryRow[];
}

/**
 * Roll-up used on the territories page header: total count, how many
 * are priority-weighted (priority ≥ 7), and how many are excluded.
 * Computed in-memory from the same list call so we don't issue
 * three separate HEAD counts.
 */
export function summariseTerritories(rows: TerritoryRow[]): {
  total: number;
  priority: number;
  excluded: number;
} {
  let priority = 0;
  let excluded = 0;
  for (const r of rows) {
    if (r.excluded) excluded += 1;
    else if (r.priority >= 7) priority += 1;
  }
  return { total: rows.length, priority, excluded };
}
