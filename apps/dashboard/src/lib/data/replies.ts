/**
 * Server-side data accessor for ``lead_replies`` (migration 0025, Part B.2).
 *
 * Reads happen via the anon/user Supabase client so RLS enforces
 * tenant isolation automatically.
 */

import { createSupabaseServerClient } from '@/lib/supabase/server';
import type { LeadReplyRow } from '@/types/db';

/**
 * Fetch all replies for a specific lead, ordered newest-first.
 * Returns an empty array if the lead has no replies yet.
 */
export async function getLeadReplies(leadId: string): Promise<LeadReplyRow[]> {
  const sb = await createSupabaseServerClient();
  const { data, error } = await sb
    .from('lead_replies')
    .select(
      'id, tenant_id, lead_id, from_email, reply_subject, body_text, ' +
        'received_at, sentiment, intent, urgency, suggested_reply, ' +
        'analysis_error, analyzed_at, created_at',
    )
    .eq('lead_id', leadId)
    .order('received_at', { ascending: false })
    .limit(50);

  if (error) {
    console.error('[replies] getLeadReplies error', error.message);
    return [];
  }
  return (data ?? []) as unknown as LeadReplyRow[];
}
