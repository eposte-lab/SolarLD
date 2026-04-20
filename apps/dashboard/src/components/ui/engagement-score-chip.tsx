/**
 * EngagementScoreChip — compact 0–100 heat indicator.
 *
 * Rendered next to the TierChip in the leads list and on the lead
 * detail header. Mirrors the three tiers from
 * ``engagementTier`` in ``lib/data/engagement.ts``:
 *
 *   hot  (≥60) → terracotta container (matches TierChip.hot)
 *   warm (≥25) → solar gold
 *   cold (< 25) → tonal neutral
 *
 * If the rollup hasn't run yet for a new lead (score==0 AND
 * updated_at is null), we render a muted "—" so the UI doesn't mis-
 * signal "cold" when we genuinely have no data.
 */

import { cn } from '@/lib/utils';
import { engagementTier } from '@/lib/data/engagement';

const TIER_STYLES = {
  hot: 'bg-secondary-container text-on-secondary-container',
  warm: 'bg-tertiary-container text-on-tertiary-container',
  cold: 'bg-surface-container-high text-on-surface-variant',
} as const;

export function EngagementScoreChip({
  score,
  updatedAt,
  className,
}: {
  score: number;
  /** ``engagement_score_updated_at`` from the lead row; null = never rolled. */
  updatedAt?: string | null;
  className?: string;
}) {
  const hasData = updatedAt !== null && updatedAt !== undefined;
  if (!hasData) {
    return (
      <span
        className={cn(
          'inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-medium',
          'bg-surface-container text-on-surface-variant opacity-60',
          className,
        )}
        title="Nessun dato di engagement ancora"
      >
        — /100
      </span>
    );
  }
  const tier = engagementTier(score);
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold tabular-nums',
        TIER_STYLES[tier],
        className,
      )}
      title={`Engagement portale ${score}/100 (${tier})`}
    >
      {score}/100
    </span>
  );
}
