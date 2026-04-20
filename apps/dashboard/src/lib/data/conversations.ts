/**
 * Server-side data accessor for WhatsApp conversations (Part B.8).
 *
 * Reads go through the Supabase server client (RLS scoped to tenant).
 * Mutations (state changes) happen via the API from the client component.
 */

import { createSupabaseServerClient } from '@/lib/supabase/server';
import type { ConversationRow } from '@/types/db';

/** List all conversations for a specific lead, newest first. */
export async function getConversationsForLead(
  leadId: string,
): Promise<ConversationRow[]> {
  const sb = await createSupabaseServerClient();
  const { data, error } = await sb
    .from('conversations')
    .select('*')
    .eq('lead_id', leadId)
    .order('last_message_at', { ascending: false })
    .limit(10);

  if (error) {
    console.error('[conversations] getConversationsForLead error', error.message);
    return [];
  }
  return (data ?? []) as ConversationRow[];
}
