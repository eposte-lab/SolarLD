/**
 * AiExecutiveInsights — rule-based actionable insights panel (V2).
 *
 * Server component. Insights computed in `getAiInsights()` — no LLM,
 * solo pattern matching su dati Supabase.
 *
 * V2 polish: liquid glass cards + Lucide icons (no emoji), tone
 * semantics chiare per warning/opportunity/info/success.
 */

import {
  AlertTriangle,
  ArrowUpRight,
  CalendarClock,
  CheckCircle2,
  Sparkle,
  Target,
} from 'lucide-react';
import Link from 'next/link';
import type { LucideIcon } from 'lucide-react';

import type { AiInsight } from '@/lib/data/geo-analytics';
import { SectionEyebrow } from '@/components/ui/section-eyebrow';
import { cn } from '@/lib/utils';

const TYPE_CONFIG: Record<
  AiInsight['type'],
  { Icon: LucideIcon; iconWrap: string; title: string }
> = {
  warning: {
    Icon: AlertTriangle,
    iconWrap: 'bg-warning/15 text-warning',
    title: 'text-on-surface',
  },
  opportunity: {
    Icon: Sparkle,
    iconWrap: 'bg-primary/15 text-primary',
    title: 'text-on-surface',
  },
  info: {
    Icon: CalendarClock,
    iconWrap: 'bg-white/[0.06] text-on-surface-variant',
    title: 'text-on-surface',
  },
  success: {
    Icon: CheckCircle2,
    iconWrap: 'bg-primary/15 text-primary',
    title: 'text-on-surface',
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
      <div className="mb-4">
        <SectionEyebrow>AI Insights · Auto</SectionEyebrow>
        <h2 className="mt-1 font-headline text-2xl font-bold tracking-tighter text-on-surface">
          Azioni prioritarie
        </h2>
      </div>

      {insights.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-2xl liquid-glass-sm py-10 px-6 relative overflow-hidden">
          <span
            className="pointer-events-none absolute inset-x-0 top-0 h-1/2 bg-glass-specular"
            aria-hidden
          />
          <div className="relative flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/15 text-primary">
            <Target size={22} strokeWidth={1.75} aria-hidden />
          </div>
          <p className="relative mt-3 text-sm font-semibold text-on-surface">
            Tutto sotto controllo
          </p>
          <p className="relative mt-1 text-[12px] text-on-surface-variant">
            Nessuna azione urgente al momento.
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {insights.map((insight, i) => {
            const config = TYPE_CONFIG[insight.type];
            const { Icon } = config;
            return (
              <div
                key={i}
                className="group relative overflow-hidden rounded-2xl liquid-glass-sm p-4 transition-all duration-300 hover:-translate-y-0.5 hover:shadow-liquid-glass"
              >
                <span
                  className="pointer-events-none absolute inset-x-0 top-0 h-12 bg-glass-specular"
                  aria-hidden
                />
                <div className="relative flex items-start gap-3">
                  <div
                    className={cn(
                      'flex h-9 w-9 shrink-0 items-center justify-center rounded-xl',
                      config.iconWrap,
                    )}
                    aria-hidden
                  >
                    <Icon size={16} strokeWidth={2} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-3">
                      <p className={cn('text-[13.5px] font-semibold leading-snug', config.title)}>
                        {insight.title}
                      </p>
                      {insight.metric && (
                        <span className="shrink-0 font-headline text-xl font-bold tabular-nums leading-none text-on-surface tracking-tightest">
                          {insight.metric}
                        </span>
                      )}
                    </div>
                    <p className="mt-1.5 text-[12.5px] leading-relaxed text-on-surface-variant">
                      {insight.body}
                    </p>
                    {insight.action_href && (
                      <Link
                        href={insight.action_href}
                        className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] px-3 py-1.5 text-[12px] font-semibold text-on-surface transition-colors"
                      >
                        {insight.action_label ?? 'Vedi'}
                        <ArrowUpRight size={12} strokeWidth={2.5} aria-hidden />
                      </Link>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
