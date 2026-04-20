/**
 * In-app notifications data access.
 *
 * Reads rows from the `notifications` table directly via Supabase
 * SSR — RLS in migration 0017 restricts the returned set to the
 * caller's tenant + personal targeted notifications.
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';

export interface NotificationRow {
  id: string;
  tenant_id: string;
  user_id: string | null;
  severity: 'info' | 'success' | 'warning' | 'error';
  title: string;
  body: string | null;
  href: string | null;
  metadata: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
}

export async function listRecentNotifications(
  limit = 20,
): Promise<NotificationRow[]> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('notifications')
    .select('*')
    .order('created_at', { ascending: false })
    .limit(limit);
  if (error) throw new Error(`listRecentNotifications: ${error.message}`);
  return (data ?? []) as NotificationRow[];
}

export async function countUnreadNotifications(): Promise<number> {
  const supabase = await createSupabaseServerClient();
  const { count, error } = await supabase
    .from('notifications')
    .select('id', { count: 'exact', head: true })
    .is('read_at', null);
  if (error) throw new Error(`countUnreadNotifications: ${error.message}`);
  return count ?? 0;
}
