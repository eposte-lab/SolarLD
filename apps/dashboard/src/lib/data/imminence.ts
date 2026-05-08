/**
 * Imminence Predictor — dashboard read-side accessors.
 *
 * Source of truth: ``lead_imminence_predictions``, populated nightly
 * at 06:30 UTC by ``imminence_predictions_cron`` (apps/api). RLS scopes
 * to the current tenant via the ``imminence_select_own_tenant`` policy.
 *
 * The dashboard /leads page joins these rows in-process against the
 * lead list to overlay an "AI" badge + reasons on the relevant rows.
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';

export interface ImminencePrediction {
  id: string;
  lead_id: string;
  imminence_score: number;
  behavioral_score: number;
  temporal_score: number;
  contextual_score: number;
  comparative_score: number;
  primary_reasons: string[];
  talking_points: string[];
  suggested_action:
    | 'call_now'
    | 'call_today'
    | 'send_followup'
    | 'wait_24h'
    | null;
  suggested_channel: 'phone' | 'email' | 'whatsapp' | null;
  best_time_to_contact: 'morning_9_11' | 'afternoon_14_17' | 'now' | null;
  actioned_at: string | null;
  action_taken: string | null;
}

/**
 * Today's predictions for the current tenant, indexed by lead_id.
 * Defaults to the deterministic-score threshold (60) so the UI surfaces
 * only the candidates the operator should actually look at this morning.
 */
export async function listTodayPredictionsByLead(
  minScore = 60,
): Promise<Map<string, ImminencePrediction>> {
  const sb = await createSupabaseServerClient();
  const today = new Date().toISOString().slice(0, 10); // YYYY-MM-DD UTC
  const { data, error } = await sb
    .from('lead_imminence_predictions')
    .select(
      'id, lead_id, imminence_score, behavioral_score, temporal_score, ' +
        'contextual_score, comparative_score, primary_reasons, talking_points, ' +
        'suggested_action, suggested_channel, best_time_to_contact, ' +
        'actioned_at, action_taken',
    )
    .eq('prediction_date', today)
    .gte('imminence_score', minScore)
    .order('imminence_score', { ascending: false });

  if (error) {
    // Migration 0118 may not be applied yet on a stale environment —
    // fail soft so the rest of /leads keeps rendering.
    console.warn('listTodayPredictions failed:', error.message);
    return new Map();
  }

  const map = new Map<string, ImminencePrediction>();
  for (const row of (data ?? []) as unknown as ImminencePrediction[]) {
    map.set(row.lead_id, row);
  }
  return map;
}
