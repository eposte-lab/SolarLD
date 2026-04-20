/**
 * Small pill-shaped badges for lead tiers and pipeline statuses.
 *
 * These are used across the list, detail, and overview pages so the
 * colour/label mapping is defined in one place.
 */

import { cn } from '@/lib/utils';
import type { LeadScoreTier, LeadStatus } from '@/types/db';

const TIER_STYLES: Record<LeadScoreTier, string> = {
  hot: 'bg-red-500/15 text-red-600 border-red-500/30',
  warm: 'bg-amber-500/15 text-amber-600 border-amber-500/30',
  cold: 'bg-blue-500/15 text-blue-600 border-blue-500/30',
  rejected: 'bg-zinc-500/15 text-zinc-500 border-zinc-500/30',
};

const TIER_LABEL: Record<LeadScoreTier, string> = {
  hot: 'HOT',
  warm: 'WARM',
  cold: 'COLD',
  rejected: 'SCARTATO',
};

export function TierBadge({ tier, className }: { tier: LeadScoreTier; className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide',
        TIER_STYLES[tier],
        className,
      )}
    >
      {TIER_LABEL[tier]}
    </span>
  );
}

const STATUS_STYLES: Record<LeadStatus, string> = {
  new: 'bg-zinc-500/10 text-zinc-500 border-zinc-500/20',
  sent: 'bg-sky-500/10 text-sky-600 border-sky-500/30',
  delivered: 'bg-sky-500/15 text-sky-700 border-sky-500/40',
  opened: 'bg-indigo-500/15 text-indigo-600 border-indigo-500/30',
  clicked: 'bg-violet-500/15 text-violet-600 border-violet-500/30',
  engaged: 'bg-emerald-500/15 text-emerald-600 border-emerald-500/30',
  whatsapp: 'bg-green-500/15 text-green-600 border-green-500/30',
  appointment: 'bg-orange-500/15 text-orange-600 border-orange-500/30',
  closed_won: 'bg-emerald-600 text-white border-emerald-700',
  closed_lost: 'bg-zinc-600 text-white border-zinc-700',
  blacklisted: 'bg-red-600 text-white border-red-700',
};

const STATUS_LABEL: Record<LeadStatus, string> = {
  new: 'Nuovo',
  sent: 'Inviato',
  delivered: 'Consegnato',
  opened: 'Aperto',
  clicked: 'Click',
  engaged: 'Engaged',
  whatsapp: 'WhatsApp',
  appointment: 'Appuntamento',
  closed_won: 'Chiuso (win)',
  closed_lost: 'Chiuso (perso)',
  blacklisted: 'Blacklist',
};

export function StatusBadge({
  status,
  className,
}: {
  status: LeadStatus;
  className?: string;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium',
        STATUS_STYLES[status],
        className,
      )}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}

export function pipelineLabel(s: LeadStatus): string {
  return STATUS_LABEL[s];
}
