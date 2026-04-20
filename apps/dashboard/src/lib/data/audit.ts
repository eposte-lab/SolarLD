/**
 * Audit log — read-side accessor for the dashboard.
 *
 * Part B.11. Writes go through the FastAPI service-role client
 * (``services/audit_service.py``). Reads are RLS-scoped: each tenant
 * sees only its own rows.
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';
import type { AuditLogRow } from '@/types/db';

const COLUMNS = 'id, action, target_table, target_id, actor_user_id, diff, at';

/**
 * Fetch the N most recent audit entries for the current tenant.
 *
 * @param limit  Max rows to return (default 100, capped at 500 to avoid
 *               hammering a very active tenant's viewport).
 */
export async function getAuditLog(limit = 100): Promise<AuditLogRow[]> {
  const sb = await createSupabaseServerClient();
  const { data, error } = await sb
    .from('audit_log')
    .select(COLUMNS)
    .order('at', { ascending: false })
    .limit(Math.min(limit, 500));

  if (error) throw new Error(`getAuditLog: ${error.message}`);
  return (data ?? []) as unknown as AuditLogRow[];
}

/**
 * Fetch all audit entries for a single lead (target_table='leads', target_id=leadId).
 * Used on the lead detail page "Zona GDPR" section.
 */
export async function getAuditLogForLead(leadId: string): Promise<AuditLogRow[]> {
  const sb = await createSupabaseServerClient();
  const { data, error } = await sb
    .from('audit_log')
    .select(COLUMNS)
    .eq('target_table', 'leads')
    .eq('target_id', leadId)
    .order('at', { ascending: false })
    .limit(50);

  if (error) throw new Error(`getAuditLogForLead: ${error.message}`);
  return (data ?? []) as unknown as AuditLogRow[];
}
