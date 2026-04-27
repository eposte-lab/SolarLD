/**
 * BadgeStatus — pill 24px con dot + text per stati operativi.
 *
 * Ispirato ai badge "Online / Offline" nei reference IMG_0925 / IMG_0920.
 * Single-accent system: solo `success` (verde desaturato) e `critical`
 * (rosso desaturato) hanno colore — il resto è grigio neutro.
 *
 * Esempi:
 *   <BadgeStatus tone="success" label="Online" />
 *   <BadgeStatus tone="critical" label="Offline" />
 *   <BadgeStatus tone="neutral"  label="Paused" />
 *   <BadgeStatus tone="warning"  label="Warm-up" />  ← amber, single-use
 */

import { cn } from '@/lib/utils';

export type BadgeTone = 'success' | 'critical' | 'warning' | 'neutral';

interface Props {
  tone?: BadgeTone;
  label: string;
  /** Hide the dot indicator (text-only pill). */
  dotless?: boolean;
  className?: string;
}

const TONE_STYLES: Record<BadgeTone, { bg: string; text: string; dot: string }> = {
  success: {
    bg: 'bg-success-container',
    text: 'text-on-success-container',
    dot: 'bg-success',
  },
  critical: {
    bg: 'bg-error-container',
    text: 'text-on-error-container',
    dot: 'bg-error',
  },
  warning: {
    // L'unico uso "legittimo" dell'amber: warm-up / cap reached / DMARC none.
    // Mint è riservato a "tutto va bene"; warning serve a distinguere
    // l'attenzione che richiede azione manuale.
    bg: 'bg-warning/15',
    text: 'text-warning',
    dot: 'bg-warning',
  },
  neutral: {
    bg: 'bg-surface-container-high',
    text: 'text-on-surface-variant',
    dot: 'bg-on-surface-variant',
  },
};

export function BadgeStatus({
  tone = 'neutral',
  label,
  dotless = false,
  className,
}: Props) {
  const styles = TONE_STYLES[tone];
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider',
        styles.bg,
        styles.text,
        className,
      )}
    >
      {!dotless && (
        <span
          className={cn('h-1.5 w-1.5 rounded-full', styles.dot)}
          aria-hidden
        />
      )}
      {label}
    </span>
  );
}
