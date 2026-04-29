/**
 * Analytics page — rollups over api_usage_log + leads + territories.
 *
 * Layout (Luminous Curator bento):
 *   Row 1: header + 4 MTD KPI chips (usage)
 *   Row 2: Funnel strip (7 stages, last 30d) + total spend chip
 *   Row 3: Spend-by-provider table + daily spend sparkline
 *   Row 4: Territory ROI table (full width)
 *
 * All data comes from the `analytics_*` Postgres RPCs (migration
 * 0016). The page is a server component — RLS + SECURITY DEFINER
 * combine to keep the tenant scoping trustworthy.
 */

import { redirect } from 'next/navigation';

import { ProviderSpendTable } from '@/components/analytics/provider-spend-table';
import { TerritoryRoiTable } from '@/components/analytics/territory-roi-table';
import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { KpiChipCard } from '@/components/ui/kpi-chip-card';
import { Sparkline } from '@/components/ui/sparkline';
import {
  getFunnel,
  getSpendByProvider,
  getSpendDaily,
  getTerritoryRoi,
  getUsageMtd,
  type FunnelCounts,
} from '@/lib/data/analytics';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import {
  formatEurPlain,
  formatNumber,
  formatPercent,
} from '@/lib/utils';

export const dynamic = 'force-dynamic';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const FUNNEL_STAGES: Array<{ key: keyof FunnelCounts; label: string }> = [
  { key: 'leads_total', label: 'Lead generati' },
  { key: 'sent', label: 'Inviati' },
  { key: 'delivered', label: 'Consegnati' },
  { key: 'opened', label: 'Aperti' },
  { key: 'clicked', label: 'Cliccati' },
  { key: 'engaged', label: 'Engaged' },
  { key: 'contract_signed', label: 'Firmati' },
];

function rate(num: number, denom: number): string {
  if (!denom) return '—';
  return formatPercent(num / denom, 1);
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default async function AnalyticsPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const [usage, funnel, providerSpend, dailySpend, territories] =
    await Promise.all([
      getUsageMtd(),
      getFunnel(30),
      getSpendByProvider(),
      getSpendDaily(30),
      getTerritoryRoi(),
    ]);

  const totalMtdCostCents = providerSpend.reduce(
    (acc, row) => acc + row.cost_cents,
    0,
  );
  const totalMtdCalls = providerSpend.reduce((acc, row) => acc + row.calls, 0);
  const totalErrors = providerSpend.reduce((acc, row) => acc + row.errors, 0);

  return (
    <div className="space-y-8">
      {/* ------------------------------------------------------------------
           Editorial header
      ------------------------------------------------------------------ */}
      <header className="flex flex-col gap-1">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Analytics · Rollup del mese
        </p>
        <h1 className="font-headline text-4xl font-bold tracking-tighter text-on-surface">
          Performance e spese
        </h1>
        <p className="mt-1 text-sm text-on-surface-variant">
          Aggregati lato Postgres (SECURITY DEFINER) su{' '}
          <span className="font-mono">api_usage_log</span>,{' '}
          <span className="font-mono">leads</span>,{' '}
          <span className="font-mono">territories</span>.
        </p>
      </header>

      {/* ------------------------------------------------------------------
           Row 1 — MTD usage KPIs
      ------------------------------------------------------------------ */}
      <BentoGrid cols={4}>
        <KpiChipCard
          label="Tetti analizzati"
          value={formatNumber(usage.roofs_scanned_mtd)}
          hint="MTD"
          accent="primary"
        />
        <KpiChipCard
          label="Lead generati"
          value={formatNumber(usage.leads_generated_mtd)}
          hint="MTD"
          accent="tertiary"
        />
        <KpiChipCard
          label="Email inviate"
          value={formatNumber(usage.emails_sent_mtd)}
          hint="MTD"
          accent="secondary"
        />
        <KpiChipCard
          label="Cartoline spedite"
          value={formatNumber(usage.postcards_sent_mtd)}
          hint="MTD"
          accent="neutral"
        />
      </BentoGrid>

      {/* ------------------------------------------------------------------
           Row 2 — Funnel (last 30d) + total spend
      ------------------------------------------------------------------ */}
      <BentoCard span="full">
        <header className="mb-5 flex items-center justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Funnel · Ultimi 30 giorni
            </p>
            <h2 className="font-headline text-2xl font-bold tracking-tighter">
              Conversione per stadio
            </h2>
          </div>
          <div className="text-right">
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Spesa totale MTD
            </p>
            <p className="font-headline text-2xl font-bold tracking-tighter text-primary">
              {formatEurPlain(totalMtdCostCents / 100)}
            </p>
          </div>
        </header>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-7">
          {FUNNEL_STAGES.map((stage, idx) => {
            const value = funnel[stage.key];
            const prevStage = idx > 0 ? FUNNEL_STAGES[idx - 1] : undefined;
            const prev = prevStage ? funnel[prevStage.key] : value;
            const conv = prevStage ? rate(value, prev) : '—';
            return (
              <div
                key={stage.key}
                className="rounded-lg bg-surface-container-low p-4"
              >
                <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  {stage.label}
                </p>
                <p className="mt-2 font-headline text-2xl font-bold leading-none tracking-tighter text-on-surface">
                  {formatNumber(value)}
                </p>
                <p className="mt-2 text-xs text-on-surface-variant">
                  {idx === 0 ? 'totali' : `${conv} vs. stadio prec.`}
                </p>
              </div>
            );
          })}
        </div>

        {/* Tier breakdown row */}
        <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
          <div className="rounded-lg bg-secondary-container/40 p-4">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-on-secondary-container">
              Hot
            </p>
            <p className="mt-2 font-headline text-xl font-bold tracking-tighter text-on-secondary-container">
              {formatNumber(funnel.hot)}
            </p>
          </div>
          <div className="rounded-lg bg-tertiary-container/40 p-4">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-on-tertiary-container">
              Warm
            </p>
            <p className="mt-2 font-headline text-xl font-bold tracking-tighter text-on-tertiary-container">
              {formatNumber(funnel.warm)}
            </p>
          </div>
          <div className="rounded-lg bg-surface-container-high p-4">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Cold
            </p>
            <p className="mt-2 font-headline text-xl font-bold tracking-tighter">
              {formatNumber(funnel.cold)}
            </p>
          </div>
          <div className="rounded-lg bg-surface-container p-4 opacity-80">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Scartati
            </p>
            <p className="mt-2 font-headline text-xl font-bold tracking-tighter">
              {formatNumber(funnel.rejected)}
            </p>
          </div>
        </div>
      </BentoCard>

      {/* ------------------------------------------------------------------
           Row 3 — Provider spend table + daily sparkline
      ------------------------------------------------------------------ */}
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
        <BentoCard>
          <header className="mb-5">
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Spesa per provider · MTD
            </p>
            <h2 className="font-headline text-xl font-bold tracking-tighter">
              Breakdown fornitori
            </h2>
            <p className="mt-1 text-xs text-on-surface-variant">
              {formatNumber(totalMtdCalls)} chiamate ·{' '}
              {totalErrors > 0 ? (
                <span className="text-secondary">
                  {formatNumber(totalErrors)} errori
                </span>
              ) : (
                'nessun errore'
              )}
            </p>
          </header>

          {providerSpend.length === 0 ? (
            <div className="rounded-lg bg-surface-container-low p-8 text-center text-sm text-on-surface-variant">
              Nessuna chiamata API questo mese.
            </div>
          ) : (
            <ProviderSpendTable rows={providerSpend} />
          )}
        </BentoCard>

        <BentoCard>
          <header className="mb-5">
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Spesa giornaliera · Ultimi 30 giorni
            </p>
            <h2 className="font-headline text-xl font-bold tracking-tighter">
              Trend
            </h2>
          </header>

          <div className="text-primary">
            <Sparkline
              values={dailySpend.map((p) => p.cost_cents)}
              width={520}
              height={120}
              className="w-full"
              ariaLabel="Trend spesa giornaliera"
            />
          </div>

          <div className="mt-4 flex items-end justify-between text-xs text-on-surface-variant">
            <div>
              <p className="text-[10px] uppercase tracking-widest">Inizio</p>
              <p className="mt-1 font-semibold text-on-surface">
                {dailySpend[0]?.day ?? '—'}
              </p>
            </div>
            <div className="text-right">
              <p className="text-[10px] uppercase tracking-widest">Oggi</p>
              <p className="mt-1 font-semibold text-on-surface">
                {dailySpend[dailySpend.length - 1]?.day ?? '—'}
              </p>
            </div>
          </div>
        </BentoCard>
      </div>

      {/* ------------------------------------------------------------------
           Row 4 — Territory ROI
      ------------------------------------------------------------------ */}
      <BentoCard span="full" padding="tight">
        <header className="px-2 pb-5 pt-2">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            ROI per territorio
          </p>
          <h2 className="font-headline text-2xl font-bold tracking-tighter">
            Ritorno per zona operativa
          </h2>
        </header>

        {territories.length === 0 ? (
          <div className="rounded-lg bg-surface-container-low p-10 text-center text-sm text-on-surface-variant">
            Nessun territorio configurato. Aggiungine uno dalla pagina
            Territories per iniziare.
          </div>
        ) : (
          <TerritoryRoiTable rows={territories} />
        )}
      </BentoCard>
    </div>
  );
}
