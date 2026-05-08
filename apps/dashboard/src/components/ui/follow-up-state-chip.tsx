/**
 * FollowUpStateChip — visual answer to "what is the system doing with this
 * lead, and when?"
 *
 * Reads `followUpState(row)` (pure helper) and renders a single chip with
 * an icon, a short label, and a tooltip carrying the full explanation
 * (cadence, next email date, why the system stopped, etc.).
 *
 * Style mirrors `engagement-score-chip.tsx`: rounded pill, Material Design
 * tonal containers, no off-palette colors.
 */

import { Bot, Flame, MessageCircle, Pause, Sparkles } from 'lucide-react';

import { followUpState, type FollowUpKind } from '@/lib/data/followup-state';
import type { LeadListRow } from '@/types/db';
import { cn } from '@/lib/utils';

// Per-kind visual config. `weight` is computed by the helper, not here.
const STYLES: Record<FollowUpKind, string> = {
  manual: 'bg-error-container text-on-error-container',
  interessato: 'bg-secondary-container text-on-secondary-container',
  engaged: 'bg-tertiary-container text-on-tertiary-container',
  lukewarm: 'bg-surface-container-high text-on-surface',
  riattivazione: 'bg-secondary-container/60 text-on-secondary-container',
  inattivo: 'bg-surface-container text-on-surface-variant opacity-70',
  conversazione: 'bg-tertiary-container/40 text-on-tertiary-container',
};

const ICONS: Record<FollowUpKind, typeof Bot> = {
  manual: Flame,
  interessato: Sparkles,
  engaged: Bot,
  lukewarm: Bot,
  riattivazione: Bot,
  inattivo: Pause,
  conversazione: MessageCircle,
};

type ChipInputs = Pick<
  LeadListRow,
  | 'engagement_score'
  | 'engagement_peak_score'
  | 'last_followup_scenario'
  | 'last_followup_sent_at'
  | 'hot_lead_alerted_at'
  | 'pipeline_status'
  | 'last_portal_event_at'
>;

export function FollowUpStateChip({
  row,
  className,
}: {
  row: ChipInputs;
  className?: string;
}) {
  const state = followUpState(row);
  const Icon = ICONS[state.kind];
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-semibold',
        STYLES[state.kind],
        className,
      )}
      title={state.tooltip}
    >
      <Icon size={11} strokeWidth={2.5} aria-hidden />
      <span>{state.label}</span>
    </span>
  );
}
