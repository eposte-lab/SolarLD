/**
 * StatusChip + TierChip — the Luminous Curator replacement for
 * `badges.tsx`.
 *
 * Per DESIGN.md §5: "Soft-rounded (md). Use tertiary-container for
 * Warm Leads and primary-container for Closed Leads. Always label-md
 * for maximum legibility."
 *
 * Unlike the legacy badges these chips carry **no 1px border** — the
 * container background is enough separation.
 */

import { cn } from '@/lib/utils';
import type { LeadScoreTier, LeadStatus } from '@/types/db';

// ---------------------------------------------------------------------------
// TierChip
// ---------------------------------------------------------------------------

const TIER_STYLES: Record<LeadScoreTier, string> = {
  // Hot → terracotta container (heat)
  hot: 'bg-secondary-container text-on-secondary-container',
  // Warm → solar gold
  warm: 'bg-tertiary-container text-on-tertiary-container',
  // Cold → tonal neutral
  cold: 'bg-surface-container-high text-on-surface-variant',
  // Rejected → muted + slightly dimmer
  rejected: 'bg-surface-container text-on-surface-variant opacity-70',
};

const TIER_LABEL: Record<LeadScoreTier, string> = {
  hot: 'HOT',
  warm: 'WARM',
  cold: 'COLD',
  rejected: 'SCARTATO',
};

export function TierChip({
  tier,
  className,
}: {
  tier: LeadScoreTier;
  className?: string;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider',
        TIER_STYLES[tier],
        className,
      )}
    >
      {TIER_LABEL[tier]}
    </span>
  );
}

// ---------------------------------------------------------------------------
// StatusChip
// ---------------------------------------------------------------------------

/**
 * Grouped by funnel stage so similar statuses share a visual family:
 *   - pre-send    → neutral surface
 *   - in-flight   → tonal (delivered / opened)
 *   - engaged     → primary-container (green)
 *   - scheduled   → tertiary-container (gold)
 *   - won         → primary gradient
 *   - lost / blk  → secondary/error container
 */
const STATUS_STYLES: Record<LeadStatus, string> = {
  new: 'bg-surface-container-high text-on-surface-variant',
  sent: 'bg-surface-container-highest text-on-surface',
  delivered: 'bg-surface-container-highest text-on-surface',
  opened: 'bg-primary-container/60 text-on-primary-container',
  clicked: 'bg-primary-container text-on-primary-container',
  engaged: 'bg-primary-container text-on-primary-container',
  whatsapp: 'bg-primary-container text-on-primary-container',
  appointment: 'bg-tertiary-container text-on-tertiary-container',
  closed_won: 'bg-primary text-on-primary',
  closed_lost: 'bg-surface-container-high text-on-surface-variant',
  blacklisted: 'bg-secondary-container text-on-secondary-container',
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

export function StatusChip({
  status,
  className,
}: {
  status: LeadStatus;
  className?: string;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md px-2.5 py-0.5 text-xs font-medium',
        STATUS_STYLES[status],
        className,
      )}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}

export function statusLabel(s: LeadStatus): string {
  return STATUS_LABEL[s];
}
