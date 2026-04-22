/**
 * PipelineRevenuePanel — estimated revenue by funnel stage.
 *
 * Server component. Uses CSS horizontal bars (no Recharts/JS required).
 * Revenue formula: Σ(estimated_kwp × €1500/kWp × stage_conversion).
 */

import type { PipelineStageRevenue } from '@/lib/data/geo-analytics';

interface PipelineRevenuePanelProps {
  stages: PipelineStageRevenue[];
  className?: string;
}

function formatEur(eur: number): string {
  if (eur >= 1_000_000) return `€${(eur / 1_000_000).toFixed(1)}M`;
  if (eur >= 1_000) return `€${(eur / 1_000).toFixed(0)}k`;
  return `€${eur}`;
}

export function PipelineRevenuePanel({ stages, className }: PipelineRevenuePanelProps) {
  const totalLeads = stages.reduce((s, st) => s + st.count, 0);
  const totalRevenue = stages.reduce((s, st) => s + st.estimated_eur, 0);
  const maxRevenue = Math.max(1, ...stages.map((s) => s.estimated_eur));

  if (totalLeads === 0) {
    return (
      <div className={className}>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Pipeline Revenue
        </p>
        <h2 className="font-headline text-2xl font-bold tracking-tighter">
          Valore stimato
        </h2>
        <div className="mt-6 flex items-center justify-center rounded-xl bg-surface-container-low py-8">
          <p className="text-sm text-on-surface-variant">Nessun lead in pipeline.</p>
        </div>
      </div>
    );
  }

  return (
    <div className={className}>
      {/* Header */}
      <div className="mb-5 flex items-end justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Pipeline Revenue · Stima
          </p>
          <h2 className="font-headline text-2xl font-bold tracking-tighter">
            Valore stimato
          </h2>
        </div>
        <div className="text-right">
          <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Pipeline totale
          </p>
          <p className="font-headline text-3xl font-bold tabular-nums tracking-tighter text-primary">
            {formatEur(totalRevenue)}
          </p>
        </div>
      </div>

      {/* Stage bars */}
      <div className="flex flex-col gap-3">
        {stages.map((stage) => {
          const pct = Math.max(2, (stage.estimated_eur / maxRevenue) * 100);
          return (
            <div key={stage.status} className="group">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-xs font-semibold text-on-surface">
                  {stage.label}
                </span>
                <div className="flex items-center gap-3 text-right">
                  <span className="text-[10px] tabular-nums text-on-surface-variant">
                    {stage.count} lead
                  </span>
                  <span className="font-headline text-sm font-bold tabular-nums text-on-surface">
                    {formatEur(stage.estimated_eur)}
                  </span>
                </div>
              </div>
              <div className="h-3 overflow-hidden rounded-full bg-surface-container-high">
                <div
                  className="h-full rounded-full transition-all duration-700 ease-out"
                  style={{
                    width: `${pct}%`,
                    backgroundColor: stage.color,
                    boxShadow: stage.status === 'won'
                      ? `0 0 8px ${stage.color}80`
                      : undefined,
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>

      {/* Footer note */}
      <p className="mt-4 text-[9px] text-on-surface-variant/50">
        Stima basata su kWp × €1 500/kWp × tasso conversione per stage.
        Non è un valore contrattuale.
      </p>
    </div>
  );
}
