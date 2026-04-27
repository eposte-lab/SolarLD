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

// ---------------------------------------------------------------------------
// Daily target cap — backs the DailyCapWidget (Sprint 2)
// ---------------------------------------------------------------------------

export interface DailyCapStats {
  /** Successful sends today (Europe/Rome day, approximated to UTC day). */
  sent_today: number;
  /** Contractual daily cap from tenants.daily_target_send_cap. */
  cap: number;
  /** Sends deferred by the cap today (events.lead.outreach_ratelimited). */
  deferred_today: number;
}

/**
 * Fetch today's send count + cap + deferred count for the current tenant.
 *
 * All counts are approximated to the current UTC day which is within ±2h
 * of Italy's Rome midnight — accurate enough for a dashboard widget.
 * Actual cap enforcement uses the Redis counter keyed to Europe/Rome date.
 */
export async function getDailyCapStats(): Promise<DailyCapStats> {
  const sb = await createSupabaseServerClient();

  // Midnight UTC today (close enough to Rome midnight for display).
  const midnightUtc = new Date();
  midnightUtc.setUTCHours(0, 0, 0, 0);
  const cutoff = midnightUtc.toISOString();

  // 1. Count today's outbound sends (status ≠ failed/cancelled = actual sends).
  const { count: sentCount, error: sentErr } = await sb
    .from('outreach_sends')
    .select('id', { count: 'exact', head: true })
    .gte('sent_at', cutoff)
    .not('status', 'in', '("failed","cancelled")');
  if (sentErr) throw new Error(`getDailyCapStats:sent: ${sentErr.message}`);

  // 2. Count today's deferred events (ratelimited by daily cap).
  const { count: deferCount, error: deferErr } = await sb
    .from('events')
    .select('id', { count: 'exact', head: true })
    .eq('event_type', 'lead.outreach_ratelimited')
    .gte('occurred_at', cutoff);
  if (deferErr) throw new Error(`getDailyCapStats:defer: ${deferErr.message}`);

  // 3. Cap from tenant row.
  const { data: tenant, error: tenErr } = await sb
    .from('tenants')
    .select('daily_target_send_cap')
    .limit(1)
    .single();
  if (tenErr) throw new Error(`getDailyCapStats:tenant: ${tenErr.message}`);

  return {
    sent_today: sentCount ?? 0,
    cap: (tenant as { daily_target_send_cap?: number | null }).daily_target_send_cap ?? 250,
    deferred_today: deferCount ?? 0,
  };
}
