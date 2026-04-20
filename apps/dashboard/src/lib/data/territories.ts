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
