/**
 * Overview — Premium Dashboard (Fase C).
 *
 * Layout:
 *   Row 1: Editorial header
 *   Row 2: Pipeline Revenue strip (5 stage bars, total value)
 *   Row 3: 2-col — GeoRadarMap (2/3) | AI Executive Insights (1/3)
 *   Row 4: 2-col — Smart Time Heatmap (1/2) | Conversion Funnel (1/2)
 *   Row 5: Full-width Lead Temperature Board (top 25, sortable)
 *
 * All heavy data fetching runs in parallel via Promise.all.
 * GeoRadarMap hydrates client-side (Mapbox GL = browser-only).
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { GradientButton } from '@/components/ui/gradient-button';
import { KpiChipCard } from '@/components/ui/kpi-chip-card';
import { SectionEyebrow } from '@/components/ui/section-eyebrow';

import { AiExecutiveInsights } from '@/components/dashboard/ai-executive-insights';
import { DailyCapWidget } from '@/components/dashboard/daily-cap-widget';
import { GeoRadarMap } from '@/components/dashboard/geo-radar-map';
import { LeadTemperatureBoard } from '@/components/dashboard/lead-temperature-board';
import { PipelineRevenuePanel } from '@/components/dashboard/pipeline-revenue-panel';
import { SmartTimeHeatmap } from '@/components/dashboard/smart-time-heatmap';

import {
  getAiInsights,
  getPipelineRevenue,
  getSendTimeHeatmap,
} from '@/lib/data/geo-analytics';
import { getConversionStats } from '@/lib/data/conversions';
import { getOverviewKpis, listLeads } from '@/lib/data/leads';
import { getContattiSummary, getScanFunnel } from '@/lib/data/contatti';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { getDailyCapStats } from '@/lib/data/usage';
import { cn, formatEurPlain, formatNumber, relativeTime } from '@/lib/utils';
import type { ConversionStats } from '@/types/db';

export const dynamic = 'force-dynamic';

export default async function DashboardOverview() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  // All data fetched in parallel — GeoRadarMap fetches its own data internally
  const [
    kpis,
    topLeads,
    conversions,
    contattiSummary,
    funnel,
    pipelineRevenue,
    heatmapCells,
    aiInsights,
    dailyCap,
  ] = await Promise.all([
    getOverviewKpis(),
    listLeads({ page: 1, pageSize: 25, filter: { tier: 'hot' } }).then((r) =>
      r.rows.length >= 10 ? r.rows : listLeads({ page: 1, pageSize: 25 }).then((r2) => r2.rows),
    ),
    getConversionStats(30),
    getContattiSummary(),
    getScanFunnel(),
    getPipelineRevenue(),
    getSendTimeHeatmap(90),
    getAiInsights(),
    getDailyCapStats(),
  ]);

  const hour = new Date().toLocaleString('it-IT', {
    timeZone: 'Europe/Rome',
    hour: 'numeric',
    hour12: false,
  });
  const greeting =
    Number(hour) < 12 ? 'Buongiorno' : Number(hour) < 18 ? 'Buon pomeriggio' : 'Buonasera';

  return (
    <div className="space-y-8">
      {/* ── Row 1: Header ─────────────────────────────────────────────────── */}
      <header className="flex flex-col gap-2">
        <SectionEyebrow>
          Panoramica · {new Date().toLocaleDateString('it-IT', {
            weekday: 'long',
            day: 'numeric',
            month: 'long',
          })}
        </SectionEyebrow>
        <div className="flex items-end justify-between gap-4">
          <h1 className="font-headline text-5xl font-bold leading-[1.05] tracking-tightest">
            <span className="text-on-surface">{greeting}, </span>
            <span className="bg-gradient-headline bg-clip-text text-transparent">
              {ctx.tenant.business_name}
            </span>
          </h1>
          <GradientButton href="/leads" variant="secondary" size="sm">
            Tutti i lead →
          </GradientButton>
        </div>
      </header>

      {/* ── Row 2: Hero KPI strip — single hero (Hot leads) + 3 default ─── */}
      <BentoGrid cols={4}>
        <KpiChipCard
          label="Hot leads"
          value={formatNumber(kpis.hot_leads)}
          hint="in pipeline"
          tone="highlight"
          size="hero"
          className="md:col-span-2"
        />
        <KpiChipCard
          label="Scansionati"
          value={formatNumber(contattiSummary.l1)}
          hint={`${formatNumber(contattiSummary.l4_qualified)} Solar OK`}
          tone="neutral"
        />
        <KpiChipCard
          label="Appuntamenti 30gg"
          value={formatNumber(kpis.appointments_30d)}
          tone="neutral"
        />
        <KpiChipCard
          label="Contratti firmati"
          value={formatNumber(kpis.closed_won_30d)}
          hint="30gg"
          tone="success"
          className="md:col-span-1"
        />
      </BentoGrid>

      {/* ── Row 2b: Daily cap SLA widget ─────────────────────────────────── */}
      <DailyCapWidget stats={dailyCap} />

      {/* ── Row 3: Pipeline Revenue (full width) ─────────────────────────── */}
      <BentoCard span="full">
        <PipelineRevenuePanel stages={pipelineRevenue} />
      </BentoCard>

      {/* ── Row 4: GeoRadarMap + AI Insights (2/3 | 1/3) ─────────────────── */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Geo Radar Map — 2 cols */}
        <div className="lg:col-span-2">
          <BentoCard span="full" className="h-full">
            <GeoRadarMap />
          </BentoCard>
        </div>

        {/* AI Insights — 1 col */}
        <div>
          <BentoCard span="full" className="h-full">
            <AiExecutiveInsights insights={aiInsights} />
          </BentoCard>
        </div>
      </div>

      {/* ── Row 5: Smart Time Heatmap + Conversion Funnel (1/2 | 1/2) ───── */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <BentoCard span="full">
          <SmartTimeHeatmap cells={heatmapCells} />
        </BentoCard>
        <BentoCard span="full">
          <ConversionFunnelCard stats={conversions} />
        </BentoCard>
      </div>

      {/* ── Row 6: Lead Temperature Board (full width) ───────────────────── */}
      <BentoCard span="full" padding="tight">
        <header className="flex items-center justify-between px-2 pb-5 pt-2">
          <div className="space-y-1">
            <SectionEyebrow>Classificazione termica</SectionEyebrow>
            <h2 className="font-headline text-2xl font-bold tracking-tighter text-on-surface">
              Lead Temperature Board
            </h2>
          </div>
          <GradientButton href="/leads" variant="secondary" size="sm">
            Tutti i lead →
          </GradientButton>
        </header>
        <LeadTemperatureBoard leads={topLeads} />
      </BentoCard>
    </div>
  );
}

// ── Conversion funnel ─────────────────────────────────────────────────────────

function formatEurFromCents(cents: number): string {
  if (cents === 0) return '—';
  return `€ ${Math.round(cents / 100).toLocaleString('it-IT')}`;
}

function conversionRate(a: number, b: number): string {
  if (a === 0) return '—';
  return `${Math.round((b / a) * 100)}%`;
}

const STAGE_LABELS: Record<string, string> = {
  booked: 'Prenotati',
  quoted: 'Quotati',
  won: 'Vinti',
  lost: 'Persi',
};

function ConversionFunnelCard({ stats }: { stats: ConversionStats }) {
  const isEmpty =
    stats.booked === 0 &&
    stats.quoted === 0 &&
    stats.won === 0 &&
    stats.lost === 0;

  return (
    <div>
      <div className="flex flex-col gap-1 md:flex-row md:items-end md:justify-between">
        <div className="space-y-1">
          <SectionEyebrow>Attribution conversioni · 30gg</SectionEyebrow>
          <h2 className="font-headline text-2xl font-bold tracking-tighter text-on-surface">
            Chiusure commerciali
          </h2>
        </div>
        {stats.won_value_cents > 0 && (
          <div className="text-right">
            <SectionEyebrow tone="dim">Pipeline chiuso</SectionEyebrow>
            <p className="font-headline text-3xl font-bold tabular-nums tracking-tightest text-primary editorial-glow">
              {formatEurFromCents(stats.won_value_cents)}
            </p>
          </div>
        )}
      </div>

      {isEmpty ? (
        <ConversionEmptyState />
      ) : (
        <div className="mt-6 flex flex-col gap-4 md:flex-row md:items-center">
          {/* Forward funnel: booked → quoted → won */}
          <div className="flex flex-1 items-center gap-2">
            {(['booked', 'quoted', 'won'] as const).map((stage, i) => (
              <div key={stage} className="flex items-center gap-2">
                {i > 0 && (
                  <div className="flex flex-col items-center gap-0.5">
                    <span className="text-lg text-on-surface-variant/40">→</span>
                    <span className="text-[10px] text-on-surface-variant/60 tabular-nums">
                      {conversionRate(
                        stage === 'quoted' ? stats.booked : stats.quoted,
                        stats[stage],
                      )}
                    </span>
                  </div>
                )}
                <div
                  className={cn(
                    'flex min-w-[72px] flex-col items-center rounded-xl px-4 py-3 ghost-border',
                    stage === 'won'
                      ? 'bg-primary/10 text-primary'
                      : 'bg-surface-container-low text-on-surface',
                  )}
                >
                  <span className="font-headline text-3xl font-bold tabular-nums tracking-tightest">
                    {stats[stage]}
                  </span>
                  <span className="mt-0.5 text-[10px] font-semibold uppercase tracking-wider">
                    {STAGE_LABELS[stage]}
                  </span>
                </div>
              </div>
            ))}
          </div>

          {/* Divider */}
          <div className="hidden h-16 w-px bg-outline-variant/30 md:block" />

          {/* Lost */}
          <div className="flex items-center gap-2 md:flex-col md:items-end">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant md:text-right">
              Persi
            </p>
            <p className="font-headline text-3xl font-bold tabular-nums tracking-tighter text-on-surface-variant">
              {stats.lost}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function ConversionEmptyState() {
  return (
    <div className="mt-5 rounded-lg border border-dashed border-outline-variant/40 bg-surface-container-lowest px-6 py-8">
      <p className="text-sm font-semibold text-on-surface">
        Nessuna conversione registrata negli ultimi 30 giorni.
      </p>
      <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
        Registra le chiusure tramite il pixel CRM o automatizzando via POST.
        Il portal registra automaticamente{' '}
        <code className="rounded bg-surface-container px-1 font-mono text-xs">
          stage=booked
        </code>{' '}
        quando un lead invia il form di sopralluogo.
      </p>
      <div className="mt-4 rounded-md bg-surface-container p-3">
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
          URL pixel (sostituisci {'{slug}'} con il public_slug del lead)
        </p>
        <code className="break-all font-mono text-xs text-on-surface">
          GET /v1/public/lead/
          <span className="text-primary">{'{slug}'}</span>
          /pixel?stage=won
        </code>
      </div>
    </div>
  );
}
