/**
 * Server-side data accessor for template A/B experiments
 * (migration 0026, Part B.4 — tier=enterprise).
 *
 * Reads go through the Supabase server client (RLS scoped to tenant).
 * Mutations and stats (which involve Bayesian Monte Carlo) are fetched
 * client-side via the browser API client in ExperimentsManager.
 */

import { createSupabaseServerClient } from '@/lib/supabase/server';
import type { ExperimentRow } from '@/types/db';

/** List all experiments for the current tenant, newest first. */
export async function listExperiments(): Promise<ExperimentRow[]> {
  const sb = await createSupabaseServerClient();
  const { data, error } = await sb
    .from('template_experiments')
    .select('*')
    .order('started_at', { ascending: false })
    .limit(100);

  if (error) {
    console.error('[experiments] listExperiments error', error.message);
    return [];
  }
  return (data ?? []) as ExperimentRow[];
}
