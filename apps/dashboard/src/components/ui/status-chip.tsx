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
  // Editorial Glass: amber-only highlights, intensity differs by tier.
  hot: 'bg-primary/15 text-primary',
  warm: 'bg-primary/8 text-primary-dim',
  cold: 'bg-surface-container-high text-on-surface-variant',
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
// Editorial Glass — single accent: amber per stati pre-win, success per won,
// error per blacklist. Pre-send/lost stay neutral grey.
const STATUS_STYLES: Record<LeadStatus, string> = {
  new: 'bg-surface-container-high text-on-surface-variant',
  sent: 'bg-surface-container-highest text-on-surface',
  delivered: 'bg-surface-container-highest text-on-surface',
  opened: 'bg-primary/8 text-primary-dim',
  clicked: 'bg-primary/12 text-primary',
  engaged: 'bg-primary/15 text-primary',
  whatsapp: 'bg-primary/15 text-primary',
  appointment: 'bg-primary/20 text-primary',
  closed_won: 'bg-success/15 text-success',
  closed_lost: 'bg-surface-container-high text-on-surface-variant',
  blacklisted: 'bg-error/15 text-error',
};

const STATUS_LABEL: Record<LeadStatus, string> = {
  new: 'Freddo',
  sent: 'Email inviata',
  delivered: 'Email consegnata',
  opened: 'Email aperta',
  clicked: 'Link cliccato',
  engaged: 'Caldo',
  whatsapp: 'WhatsApp attivo',
  appointment: 'Appuntamento fissato',
  closed_won: 'Contratto firmato',
  closed_lost: 'Perso',
  blacklisted: 'Blacklist',
};

/**
 * Maps each system status to a 0-based commercial pipeline bucket.
 *
 * Bucket 0 — "nuovo"        lead appena qualificato, non ancora contattato
 * Bucket 1 — "contattato"   primo contatto inviato / aperto / cliccato
 * Bucket 2 — "in-valutazione" ha interagito (portal, WA, risposta)
 * Bucket 3 — "preventivo"   appuntamento fissato / trattativa aperta
 * Bucket 4 — "chiuso"       contratto firmato, perso o blacklist
 *
 * When `pipeline_labels` is passed to <StatusChip>, bucket N shows
 * `pipeline_labels[N]` instead of the hardcoded English label.
 */
export const STATUS_BUCKET: Record<LeadStatus, number> = {
  new: 0,
  sent: 1,
  delivered: 1,
  opened: 1,
  clicked: 1,
  engaged: 2,
  whatsapp: 2,
  appointment: 3,
  closed_won: 4,
  closed_lost: 4,
  blacklisted: 4,
};

/**
 * Return the display label for a system status, optionally overriding
 * it with the tenant's custom pipeline vocabulary.
 *
 * @param status - The system `LeadStatus` value stored in the DB.
 * @param pipelineLabels - The 5-item array from `CRMConfig.pipeline_labels`.
 *   When provided and the bucket index resolves to a non-empty string, that
 *   label is returned; otherwise falls back to the hardcoded Italian default.
 */
export function getPipelineLabel(
  status: LeadStatus,
  pipelineLabels?: string[],
): string {
  if (pipelineLabels && pipelineLabels.length > 0) {
    const bucket = STATUS_BUCKET[status];
    const custom = pipelineLabels[bucket];
    if (custom && custom.trim().length > 0) return custom.trim();
  }
  return STATUS_LABEL[status];
}

export function StatusChip({
  status,
  pipelineLabels,
  className,
}: {
  status: LeadStatus;
  /** Optional tenant pipeline vocabulary — overrides the default label. */
  pipelineLabels?: string[];
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
      {getPipelineLabel(status, pipelineLabels)}
    </span>
  );
}

export function statusLabel(s: LeadStatus, pipelineLabels?: string[]): string {
  return getPipelineLabel(s, pipelineLabels);
}
