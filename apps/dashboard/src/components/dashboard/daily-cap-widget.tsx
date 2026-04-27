/**
 * DailyCapWidget — mostra il consumo giornaliero "in-target" rispetto al cap.
 *
 * Sprint 2 SLA: ogni tenant ha diritto a ~250 email/giorno in target.
 * Questo widget traduce il contatore Redis in un numero visibile per l'installatore.
 *
 * Server component: i dati vengono letti al momento del render della pagina.
 * Non serve stato client — il contatore si aggiorna ad ogni navigazione.
 */

import Link from 'next/link';

import { SectionEyebrow } from '@/components/ui/section-eyebrow';
import type { DailyCapStats } from '@/lib/data/usage';
import { cn } from '@/lib/utils';

interface Props {
  stats: DailyCapStats;
  /** Collassa il widget in un banner compatto (per la pagina campagna). */
  compact?: boolean;
}

export function DailyCapWidget({ stats, compact = false }: Props) {
  const { sent_today, cap, deferred_today } = stats;
  const pct = cap > 0 ? Math.min(1, sent_today / cap) : 0;
  const pctDisplay = Math.round(pct * 100);

  // Color coding: amber < 90%, error >= 90%. La success green è riservata a eventi
  // semantici "won/online", non al cap quotidiano.
  const barColor = pct < 0.9 ? 'bg-primary' : 'bg-error';
  const textColor = pct < 0.9 ? 'text-primary' : 'text-error';

  if (compact) {
    return (
      <div className="flex items-center gap-3 rounded-xl glass-panel-sm px-4 py-3">
        <div className="min-w-0 flex-1">
          <SectionEyebrow tone="dim">Invii in target oggi</SectionEyebrow>
          <div className="mt-1 flex items-baseline gap-1.5">
            <span className={cn('font-headline text-2xl font-bold tabular-nums tracking-tightest', textColor)}>
              {sent_today}
            </span>
            <span className="text-sm text-on-surface-variant">/ {cap}</span>
          </div>
        </div>
        {/* Mini progress bar */}
        <div className="h-1.5 w-20 shrink-0 overflow-hidden rounded-full bg-white/8">
          <div
            className={cn('h-full rounded-full transition-all', barColor)}
            style={{ width: `${pctDisplay}%` }}
          />
        </div>
        {deferred_today > 0 && (
          <Link
            href="/invii?tab=rimandati"
            className="shrink-0 rounded-lg bg-primary/15 px-2 py-1 text-[10px] font-semibold text-primary hover:bg-primary/25"
          >
            {deferred_today} rimand.
          </Link>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-2xl glass-panel px-5 py-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <SectionEyebrow>Invii in target oggi · Europe/Rome</SectionEyebrow>
          <div className="mt-1 flex items-baseline gap-2">
            <span className={cn('font-headline text-4xl font-bold tabular-nums tracking-tightest', textColor)}>
              {sent_today}
            </span>
            <span className="text-lg text-on-surface-variant">/ {cap}</span>
            <span className="text-sm text-on-surface-variant">({pctDisplay}%)</span>
          </div>
          {deferred_today > 0 && (
            <p className="mt-1 text-xs text-on-surface-variant">
              +{deferred_today} rimandati a domani →{' '}
              <Link href="/invii?tab=rimandati" className="font-semibold text-primary underline-offset-2 hover:underline">
                vedi lista
              </Link>
            </p>
          )}
        </div>

        {pct >= 1 && (
          <span className="shrink-0 rounded-full bg-error-container px-3 py-1 text-[11px] font-bold text-on-error-container">
            Cap raggiunto
          </span>
        )}
      </div>

      {/* Progress bar */}
      <div className="mt-4 h-2 overflow-hidden rounded-full bg-white/8">
        <div
          className={cn('h-full rounded-full transition-all duration-500', barColor)}
          style={{ width: `${pctDisplay}%` }}
        />
      </div>

      <div className="mt-2 flex justify-between text-[10px] text-on-surface-variant">
        <span>0</span>
        <span className="font-semibold">{cap} SLA</span>
      </div>
    </div>
  );
}
