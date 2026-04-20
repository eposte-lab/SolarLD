/**
 * CRM webhooks — read-side accessors for the dashboard.
 *
 * Writes (create / update / rotate / delete) go through the FastAPI
 * route ``/v1/crm-webhooks`` (``lib/api-client.ts``) because they need
 * the secret-generation / secret-rotation logic that only lives
 * server-side. **Reads** happen directly from Supabase with RLS so
 * the list page stays SSR and doesn't burn an extra round-trip.
 *
 * The ``secret`` column is never selected here — it's only returned
 * once at creation time (and on rotate) via the FastAPI route. If the
 * operator loses it, they rotate.
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';
import type { CrmWebhookDeliveryRow, CrmWebhookRow } from '@/types/db';

const LIST_COLUMNS =
  'id, label, url, events, active, last_status, last_delivered_at, ' +
  'failure_count, created_at, updated_at';

const DELIVERY_COLUMNS =
  'id, event_type, attempt, status_code, error, occurred_at';

/** All CRM webhook subscriptions for the current tenant (RLS-scoped). */
export async function listCrmWebhooks(): Promise<CrmWebhookRow[]> {
  const sb = await createSupabaseServerClient();
  const { data, error } = await sb
    .from('crm_webhook_subscriptions')
    .select(LIST_COLUMNS)
    .order('created_at', { ascending: false });
  if (error) throw new Error(`listCrmWebhooks: ${error.message}`);
  return (data ?? []) as unknown as CrmWebhookRow[];
}

/**
 * Last ``limit`` delivery attempts for a subscription. Ownership is
 * enforced at the RLS layer, so a caller from tenant A asking for a
 * subscription_id belonging to tenant B gets an empty list.
 */
export async function listCrmWebhookDeliveries(
  subscriptionId: string,
  limit = 50,
): Promise<CrmWebhookDeliveryRow[]> {
  const sb = await createSupabaseServerClient();
  const { data, error } = await sb
    .from('crm_webhook_deliveries')
    .select(DELIVERY_COLUMNS)
    .eq('subscription_id', subscriptionId)
    .order('occurred_at', { ascending: false })
    .limit(limit);
  if (error) throw new Error(`listCrmWebhookDeliveries: ${error.message}`);
  return (data ?? []) as unknown as CrmWebhookDeliveryRow[];
}
