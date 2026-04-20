/**
 * Domain reputation — read-only accessor for the dashboard.
 *
 * Backed by ``domain_reputation`` (migration 0020), written nightly
 * by the Python ``reputation_digest_cron`` at 02:30 UTC. RLS scopes
 * SELECT to the caller's tenant, so we never pass ``tenant_id``.
 *
 * If no snapshot exists yet (new tenant, cron hasn't run), we return
 * null — the UI renders a "No data yet" card instead of stale zeros.
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';
import type { DomainReputationRow } from '@/types/db';

/**
 * Latest reputation snapshot for the tenant. Returns null when the
 * cron has never written a row for this tenant (cold start).
 *
 * There can only be one row per (tenant, domain, date). If the tenant
 * has changed domain recently, we return the newest row regardless —
 * the caller can compare ``row.email_from_domain`` to the live
 * ``tenants.email_from_domain`` to detect a domain switch.
 */
export async function getLatestDomainReputation(): Promise<DomainReputationRow | null> {
  const sb = await createSupabaseServerClient();
  const { data, error } = await sb
    .from('domain_reputation')
    .select(
      'id, tenant_id, email_from_domain, as_of_date, ' +
        'sent_count, delivered_count, bounced_count, complained_count, opened_count, ' +
        'delivery_rate, bounce_rate, complaint_rate, open_rate, ' +
        'alarm_bounce, alarm_complaint, created_at',
    )
    .order('as_of_date', { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error) {
    throw new Error(`getLatestDomainReputation: ${error.message}`);
  }
  return (data as DomainReputationRow | null) ?? null;
}

/**
 * Up to ``limit`` historical snapshots (newest first). Used for the
 * sparkline in the reputation card if we decide to render it later.
 */
export async function listRecentDomainReputation(
  limit = 14,
): Promise<DomainReputationRow[]> {
  const sb = await createSupabaseServerClient();
  const { data, error } = await sb
    .from('domain_reputation')
    .select(
      'id, tenant_id, email_from_domain, as_of_date, ' +
        'sent_count, delivered_count, bounced_count, complained_count, opened_count, ' +
        'delivery_rate, bounce_rate, complaint_rate, open_rate, ' +
        'alarm_bounce, alarm_complaint, created_at',
    )
    .order('as_of_date', { ascending: false })
    .limit(limit);
  if (error) {
    throw new Error(`listRecentDomainReputation: ${error.message}`);
  }
  return (data ?? []) as unknown as DomainReputationRow[];
}
