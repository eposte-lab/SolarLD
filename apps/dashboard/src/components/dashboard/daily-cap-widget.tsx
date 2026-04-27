/**
 * DailyCapWidget — consumo giornaliero "in target" rispetto al cap (V2 Liquid Glass).
 *
 * Sprint 2 SLA: ogni tenant ha diritto a ~250 email/giorno in target.
 * Il widget traduce il contatore Redis in un numero leggibile dall'installatore.
 *
 * Server component — i dati vengono letti al render della pagina.
 */

import { ArrowUpRight } from 'lucide-react';
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

  // Colori semantici:
  //   < 70% → mint (sicurezza piena)
  //   70-90% → warning (attenzione, vicino al cap)
  //   ≥ 90% → error (cap raggiunto)
  const tone =
    pct < 0.7 ? 'mint' : pct < 0.9 ? 'warning' : 'error';

  const barColor =
    tone === 'mint'
      ? 'bg-primary'
      : tone === 'warning'
        ? 'bg-warning'
        : 'bg-error';
  const textColor =
    tone === 'mint'
      ? 'text-primary'
      : tone === 'warning'
        ? 'text-warning'
        : 'text-error';

  if (compact) {
    return (
      <div className="relative overflow-hidden rounded-2xl liquid-glass-sm px-5 py-4 flex items-center gap-4">
        <span
          className="pointer-events-none absolute inset-x-0 top-0 h-1/2 bg-glass-specular"
          aria-hidden
        />
        <div className="relative min-w-0 flex-1">
          <SectionEyebrow tone="dim">Invii in target oggi</SectionEyebrow>
          <div className="mt-1.5 flex items-baseline gap-1.5">
            <span
              className={cn(
                'font-headline text-3xl font-bold tabular-nums tracking-tightest',
                textColor,
              )}
            >
              {sent_today}
            </span>
            <span className="text-sm text-on-surface-variant">/ {cap}</span>
          </div>
        </div>
        <div className="relative h-1.5 w-24 shrink-0 overflow-hidden rounded-full bg-white/[0.06]">
          <div
            className={cn('h-full rounded-full transition-all duration-700', barColor)}
            style={{ width: `${pctDisplay}%` }}
          />
        </div>
        {deferred_today > 0 && (
          <Link
            href="/invii?tab=rimandati"
            className="relative shrink-0 inline-flex items-center gap-1 rounded-full bg-warning/15 px-2.5 py-1 text-[11px] font-semibold text-warning hover:bg-warning/25 transition-colors"
          >
            {deferred_today} rimand.
            <ArrowUpRight size={11} strokeWidth={2.5} aria-hidden />
          </Link>
        )}
      </div>
    );
  }

  return (
    <div className="relative overflow-hidden rounded-2xl liquid-glass px-6 py-5">
      <span
        className="pointer-events-none absolute inset-x-0 top-0 h-[40%] bg-glass-specular"
        aria-hidden
      />
      <div className="relative">
        <div className="flex items-start justify-between gap-4">
          <div>
            <SectionEyebrow>Invii in target oggi · Europe/Rome</SectionEyebrow>
            <div className="mt-1.5 flex items-baseline gap-2.5">
              <span
                className={cn(
                  'font-headline text-5xl font-bold tabular-nums tracking-tightest',
                  textColor,
                  tone === 'mint' && 'editorial-glow',
                )}
              >
                {sent_today}
              </span>
              <span className="text-lg text-on-surface-variant">/ {cap}</span>
              <span className="text-sm text-on-surface-variant">({pctDisplay}%)</span>
            </div>
            {deferred_today > 0 && (
              <p className="mt-1.5 text-[12px] text-on-surface-variant">
                +{deferred_today} rimandati a domani ·{' '}
                <Link
                  href="/invii?tab=rimandati"
                  className="font-semibold text-primary underline-offset-2 hover:underline"
                >
                  vedi lista
                </Link>
              </p>
            )}
          </div>

          {pct >= 1 && (
            <span className="shrink-0 inline-flex items-center gap-1.5 rounded-full bg-error/15 px-3 py-1 text-[11px] font-semibold uppercase tracking-wider text-error">
              <span className="h-1.5 w-1.5 rounded-full bg-error animate-pulse" aria-hidden />
              Cap raggiunto
            </span>
          )}
        </div>

        {/* Progress bar */}
        <div className="mt-5 h-2 overflow-hidden rounded-full bg-white/[0.06]">
          <div
            className={cn('h-full rounded-full transition-all duration-700', barColor)}
            style={{ width: `${pctDisplay}%` }}
          />
        </div>

        <div className="mt-2 flex justify-between text-[10px] uppercase tracking-[0.18em] text-on-surface-muted">
          <span>0</span>
          <span className="font-semibold text-on-surface-variant">{cap} SLA</span>
        </div>
      </div>
    </div>
  );
}
