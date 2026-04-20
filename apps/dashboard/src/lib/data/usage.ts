/**
 * Month-to-date usage aggregates — used by the dashboard to decide
 * whether a tenant has room in its monthly budget before triggering
 * a paid action (scan, bulk outreach, ...).
 *
 * Backed by `api_usage_log` (migration 0010). RLS restricts the
 * SELECT to the caller's tenant, so we never pass `tenant_id`
 * explicitly — the client is already bound by the SSR session.
 *
 * NB: the numbers here are *informational* for the UX gate. The
 * authoritative budget enforcement lives in the Python agents, which
 * can see all tenants via the service role and double-check before
 * each API call.
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';

/** Providers whose cost is charged to the "scan" budget bucket. */
const SCAN_PROVIDERS = ['google_solar', 'mapbox', 'overpass'] as const;

/** Providers whose cost is charged to the "outreach" budget bucket. */
const OUTREACH_PROVIDERS = ['resend', 'pixartprinting', 'neverbounce'] as const;

async function sumCostCents(
  tenantId: string,
  providers: readonly string[],
): Promise<number> {
  const sb = await createSupabaseServerClient();
  const { data, error } = await sb
    .from('api_usage_log')
    .select('cost_cents, provider, occurred_at')
    .eq('tenant_id', tenantId)
    .in('provider', providers as readonly string[] as string[])
    .gte('occurred_at', startOfMonthIso());
  if (error) throw new Error(`sumCostCents: ${error.message}`);
  let total = 0;
  for (const row of data ?? []) {
    total += row.cost_cents ?? 0;
  }
  return total;
}

function startOfMonthIso(): string {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), 1).toISOString();
}

/** Month-to-date scan spend (google_solar + mapbox + overpass). */
export async function getScanUsageMtdCents(tenantId: string): Promise<number> {
  return sumCostCents(tenantId, SCAN_PROVIDERS);
}

/** Month-to-date outreach spend (resend + pixart + neverbounce). */
export async function getOutreachUsageMtdCents(tenantId: string): Promise<number> {
  return sumCostCents(tenantId, OUTREACH_PROVIDERS);
}
