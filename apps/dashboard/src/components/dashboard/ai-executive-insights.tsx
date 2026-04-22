/**
 * AiExecutiveInsights — rule-based actionable insights panel.
 *
 * Server component. Insights are computed from Supabase queries in
 * getAiInsights() — no LLM call, just pattern matching on real data.
 *
 * Displays up to 5 prioritised insight cards with type icons,
 * metric callout, and action CTA.
 */

import Link from 'next/link';

import type { AiInsight } from '@/lib/data/geo-analytics';
import { cn } from '@/lib/utils';

// ── icons (inline SVG to avoid icon-lib dep) ────────────────────────────────

const TYPE_CONFIG: Record<
  AiInsight['type'],
  { icon: string; bg: string; text: string; border: string }
> = {
  warning: {
    icon: '⚠️',
    bg: 'bg-secondary-container/40',
    text: 'text-on-secondary-container',
    border: 'border-secondary-container',
  },
  opportunity: {
    icon: '⚡',
    bg: 'bg-tertiary-container/40',
    text: 'text-on-tertiary-container',
    border: 'border-tertiary-container',
  },
  info: {
    icon: '📅',
    bg: 'bg-surface-container-high',
    text: 'text-on-surface',
    border: 'border-outline-variant/30',
  },
  success: {
    icon: '✅',
    bg: 'bg-primary-container/30',
    text: 'text-on-primary-container',
    border: 'border-primary-container',
  },
};

interface AiExecutiveInsightsProps {
  insights: AiInsight[];
  className?: string;
}

export function AiExecutiveInsights({
  insights,
  className,
}: AiExecutiveInsightsProps) {
  return (
    <div className={className}>
      {/* Header */}
      <div className="mb-4">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          AI Insights · Auto
        </p>
        <h2 className="font-headline text-2xl font-bold tracking-tighter">
          Azioni prioritarie
        </h2>
      </div>

      {/* Insight cards */}
      {insights.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl bg-surface-container-low py-8">
          <p className="text-2xl">🎯</p>
          <p className="mt-2 text-sm font-semibold text-on-surface">
            Tutto sotto controllo
          </p>
          <p className="mt-1 text-xs text-on-surface-variant">
            Nessuna azione urgente al momento.
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-2.5">
          {insights.map((insight, i) => {
            const config = TYPE_CONFIG[insight.type];
            return (
              <div
                key={i}
                className={cn(
                  'group flex flex-col gap-2 rounded-xl border p-3.5 transition-all',
                  config.bg,
                  config.border,
                )}
              >
                {/* Top row: icon + title + metric */}
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-start gap-2 min-w-0">
                    <span className="mt-0.5 shrink-0 text-base leading-none">
                      {config.icon}
                    </span>
                    <p
                      className={cn(
                        'text-sm font-semibold leading-snug',
                        config.text,
                      )}
                    >
                      {insight.title}
                    </p>
                  </div>
                  {insight.metric && (
                    <span
                      className={cn(
                        'shrink-0 font-headline text-xl font-bold tabular-nums leading-none',
                        config.text,
                      )}
                    >
                      {insight.metric}
                    </span>
                  )}
                </div>

                {/* Body */}
                <p className="text-xs leading-relaxed text-on-surface-variant">
                  {insight.body}
                </p>

                {/* CTA */}
                {insight.action_href && (
                  <Link
                    href={insight.action_href}
                    className={cn(
                      'self-start rounded-lg px-3 py-1 text-xs font-semibold transition-colors',
                      'bg-surface-container-lowest/60 hover:bg-surface-container-lowest text-on-surface',
                    )}
                  >
                    {insight.action_label ?? 'Vedi →'}
                  </Link>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
