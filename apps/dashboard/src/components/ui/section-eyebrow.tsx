/**
 * SectionEyebrow — micro-uppercase tracked label con optional icon.
 *
 * Sostituisce la ripetizione 5+ volte sparsa nelle pagine di:
 *   <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
 *
 * Variants:
 *   - default → on-surface-variant (#8A9094)
 *   - amber   → primary tint (highlight per metriche eyebrow critiche)
 *   - dim     → on-surface-muted (#5A6066) per breadcrumb-like
 *
 * Esempio:
 *   <SectionEyebrow>Panoramica · Lunedì 27 Aprile</SectionEyebrow>
 *   <SectionEyebrow tone="amber" icon={<AlertIcon/>}>Anomalie 24h</SectionEyebrow>
 */

import { cn } from '@/lib/utils';

interface Props {
  children: React.ReactNode;
  tone?: 'default' | 'amber' | 'dim';
  icon?: React.ReactNode;
  className?: string;
  as?: 'p' | 'div' | 'span';
}

const TONE_TEXT = {
  default: 'text-on-surface-variant',
  amber: 'text-primary',
  dim: 'text-on-surface-muted',
} as const;

export function SectionEyebrow({
  children,
  tone = 'default',
  icon,
  className,
  as: As = 'p',
}: Props) {
  return (
    <As
      className={cn(
        'inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-widest',
        TONE_TEXT[tone],
        className,
      )}
    >
      {icon && <span className="flex h-3 w-3 items-center justify-center" aria-hidden>{icon}</span>}
      {children}
    </As>
  );
}
