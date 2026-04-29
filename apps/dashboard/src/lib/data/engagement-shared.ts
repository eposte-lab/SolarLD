/**
 * Engagement helpers safe to import from client components.
 *
 * The full server module ``./engagement.ts`` is marked ``server-only``
 * because it queries ``portal_events`` via the SSR Supabase client. The
 * pure utility below — bucketing a numeric score into hot/warm/cold —
 * needs to run in the browser too (chips, badges) so it lives here.
 *
 * Keep the thresholds in sync with ``compute_score`` in
 * ``apps/api/src/services/engagement_service.py``.
 */

export type EngagementTier = 'hot' | 'warm' | 'cold';

/** Human-readable tier for an engagement score (0–100). */
export function engagementTier(score: number): EngagementTier {
  if (score >= 60) return 'hot';
  if (score >= 25) return 'warm';
  return 'cold';
}
