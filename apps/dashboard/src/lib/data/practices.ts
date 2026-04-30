/**
 * Server-side data access for GSE practices and deadlines.
 *
 * Reads directly from Supabase (RLS scoped to the calling user's tenant).
 * Server-only — do not import in client components.
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';

export interface PracticeDeadlineSummary {
  id: string;
  practice_id: string;
  deadline_kind: string;
  due_at: string;
  status: 'open' | 'satisfied' | 'overdue' | 'cancelled';
  title: string;
  reference: string | null;
  practice_number: string | null;
}

/**
 * Returns open + overdue deadlines for the current tenant, ordered by urgency.
 *
 * Used by the home-page ScadenzeUrgentiWidget (server component) and the
 * /scadenze page SSR seed.  Client components should hit
 * GET /v1/practice-deadlines instead.
 */
export async function listUrgentDeadlines(
  limit = 5,
): Promise<PracticeDeadlineSummary[]> {
  const supabase = await createSupabaseServerClient();

  const { data, error } = await supabase
    .from('practice_deadlines')
    .select('id, practice_id, deadline_kind, due_at, status, metadata, practices(practice_number)')
    .in('status', ['open', 'overdue'])
    .order('due_at', { ascending: true })
    .limit(limit);

  if (error) {
    // Soft-fail: home page should not break if this table doesn't exist
    // yet (e.g. before 0085 migration is applied to the dev env).
    console.error('listUrgentDeadlines error:', error.message);
    return [];
  }

  return (data ?? []).map((row): PracticeDeadlineSummary => {
    const meta = (row.metadata ?? {}) as Record<string, unknown>;
    const practiceEmbed = row.practices as unknown;
    const practice = (Array.isArray(practiceEmbed) ? (practiceEmbed[0] ?? {}) : (practiceEmbed ?? {})) as Record<string, unknown>;
    return {
      id: String(row.id),
      practice_id: String(row.practice_id),
      deadline_kind: row.deadline_kind,
      due_at: row.due_at,
      status: row.status as PracticeDeadlineSummary['status'],
      title: (meta['title'] as string | undefined) ?? row.deadline_kind,
      reference: (meta['reference'] as string | undefined) ?? null,
      practice_number: (practice['practice_number'] as string | undefined) ?? null,
    };
  });
}

export async function countOpenDeadlines(): Promise<{
  open: number;
  overdue: number;
}> {
  const supabase = await createSupabaseServerClient();

  const { data, error } = await supabase
    .from('practice_deadlines')
    .select('status')
    .in('status', ['open', 'overdue']);

  if (error) return { open: 0, overdue: 0 };

  const rows = data ?? [];
  return {
    open: rows.filter((r) => r.status === 'open').length,
    overdue: rows.filter((r) => r.status === 'overdue').length,
  };
}
