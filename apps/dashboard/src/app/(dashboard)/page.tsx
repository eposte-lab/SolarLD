/**
 * Overview page — Luminous Curator bento grid (Fase B).
 *
 * Layout:
 *   Row 1: header + welcome card (spans 2) + 4 KPI chips
 *   Row 2: Top 10 Hot Leads table (spans full)
 *
 * The table itself keeps the existing data join (KPIs + listTopHotLeads)
 * but is wrapped in a BentoCard with no borders — rows separated by
 * 1px `ghost-border` fallback since density is the key value prop here.
 *
 * Server component: RLS-scoped reads via Supabase SSR, no client JS.
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { GradientButton } from '@/components/ui/gradient-button';
import { KpiChipCard } from '@/components/ui/kpi-chip-card';
import { StatusChip, TierChip } from '@/components/ui/status-chip';
import { getConversionStats } from '@/lib/data/conversions';
import { getOverviewKpis, listTopHotLeads } from '@/lib/data/leads';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { cn, formatNumber, relativeTime } from '@/lib/utils';
import type { ConversionStats } from '@/types/db';

export const dynamic = 'force-dynamic';

export default async function DashboardOverview() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const [kpis, topLeads, conversions] = await Promise.all([
    getOverviewKpis(),
    listTopHotLeads(10),
    getConversionStats(30),
  ]);

  return (
    <div className="space-y-8">
      {/* ------------------------------------------------------------------
           Editorial header + welcome gradient card
      ------------------------------------------------------------------ */}
      <header className="flex flex-col gap-1">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Panoramica · Ultimi 30 giorni
        </p>
        <h1 className="font-headline text-4xl font-bold tracking-tighter text-on-surface">
          Buongiorno, {ctx.tenant.business_name}
        </h1>
      </header>

      {/* ------------------------------------------------------------------
           KPI bento strip — 4 chips
      ------------------------------------------------------------------ */}
      <BentoGrid cols={4}>
        <KpiChipCard
          label="Leads inviati"
          value={formatNumber(kpis.leads_sent_30d)}
          hint="30gg"
          accent="primary"
        />
        <KpiChipCard
          label="Hot leads"
          value={formatNumber(kpis.hot_leads)}
          hint="in pipeline"
          accent="secondary"
        />
        <KpiChipCard
          label="Appuntamenti"
          value={formatNumber(kpis.appointments_30d)}
          hint="30gg"
          accent="tertiary"
        />
        <KpiChipCard
          label="Contratti firmati"
          value={formatNumber(kpis.closed_won_30d)}
          hint="30gg"
          accent="primary"
        />
      </BentoGrid>

      {/* ------------------------------------------------------------------
           Conversion attribution funnel (Part B.6)
      ------------------------------------------------------------------ */}
      <ConversionFunnelCard stats={conversions} />

      {/* ------------------------------------------------------------------
           Hot leads table — bento card, no grid dividers
      ------------------------------------------------------------------ */}
      <BentoCard span="full" padding="tight">
        <header className="flex items-center justify-between px-2 pb-5 pt-2">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Priorità outbound
            </p>
            <h2 className="font-headline text-2xl font-bold tracking-tighter">
              Top 10 Hot Leads
            </h2>
          </div>
          <GradientButton href="/leads?tier=hot" variant="secondary" size="sm">
            Vedi tutti
          </GradientButton>
        </header>

        {topLeads.length === 0 ? (
          <div className="rounded-lg bg-surface-container-low p-10 text-center">
            <p className="text-sm text-on-surface-variant">
              Nessun lead hot ancora.{' '}
              <Link
                href="/territories"
                className="font-semibold text-primary hover:underline"
              >
                Connetti un territorio
              </Link>{' '}
              per iniziare.
            </p>
          </div>
        ) : (
          <div className="overflow-hidden rounded-lg bg-surface-container-low">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  <th className="px-5 py-3">Lead</th>
                  <th className="px-5 py-3">Comune</th>
                  <th className="px-5 py-3">Score</th>
                  <th className="px-5 py-3">Tier</th>
                  <th className="px-5 py-3">Stato</th>
                  <th className="px-5 py-3">Ultimo tocco</th>
                  <th />
                </tr>
              </thead>
              <tbody className="bg-surface-container-lowest">
                {topLeads.map((lead, idx) => {
                  const name =
                    lead.subjects?.business_name ||
                    [
                      lead.subjects?.owner_first_name,
                      lead.subjects?.owner_last_name,
                    ]
                      .filter(Boolean)
                      .join(' ') ||
                    '—';
                  const lastTouch =
                    lead.dashboard_visited_at ||
                    lead.outreach_opened_at ||
                    lead.outreach_sent_at ||
                    lead.created_at;
                  return (
                    <tr
                      key={lead.id}
                      className={`transition-colors hover:bg-surface-container-low ${
                        idx !== 0 ? 'ghost-border border-t-0 border-x-0 border-b-0' : ''
                      }`}
                      style={
                        idx !== 0
                          ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }
                          : undefined
                      }
                    >
                      <td className="px-5 py-4 font-semibold text-on-surface">
                        {name}
                      </td>
                      <td className="px-5 py-4 text-on-surface-variant">
                        {lead.roofs?.comune ?? '—'}
                      </td>
                      <td className="px-5 py-4 font-headline font-bold tabular-nums">
                        {lead.score}
                      </td>
                      <td className="px-5 py-4">
                        <TierChip tier={lead.score_tier} />
                      </td>
                      <td className="px-5 py-4">
                        <StatusChip status={lead.pipeline_status} />
                      </td>
                      <td className="px-5 py-4 text-xs text-on-surface-variant">
                        {relativeTime(lastTouch)}
                      </td>
                      <td className="px-5 py-4 text-right">
                        <Link
                          href={`/leads/${lead.id}`}
                          className="text-xs font-semibold text-primary hover:underline"
                        >
                          apri →
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </BentoCard>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Conversion funnel card — closed-loop attribution (Part B.6)
// ---------------------------------------------------------------------------

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
    <BentoCard span="full">
      <div className="flex flex-col gap-1 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Attribution conversioni · 30gg
          </p>
          <h2 className="font-headline text-2xl font-bold tracking-tighter">
            Chiusure commerciali
          </h2>
        </div>
        {stats.won_value_cents > 0 && (
          <div className="text-right">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Pipeline chiuso
            </p>
            <p className="font-headline text-3xl font-bold tabular-nums tracking-tighter text-primary">
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
                    'flex min-w-[72px] flex-col items-center rounded-xl px-4 py-3',
                    stage === 'won'
                      ? 'bg-primary-container text-on-primary-container'
                      : 'bg-surface-container text-on-surface',
                  )}
                >
                  <span
                    className={cn(
                      'font-headline text-3xl font-bold tabular-nums tracking-tighter',
                      stage === 'won' ? '' : 'text-on-surface',
                    )}
                  >
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

          {/* Lost — separate bucket */}
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
    </BentoCard>
  );
}

function ConversionEmptyState() {
  return (
    <div className="mt-5 rounded-lg border border-dashed border-outline-variant/40 bg-surface-container-lowest px-6 py-8">
      <p className="text-sm font-semibold text-on-surface">
        Nessuna conversione registrata negli ultimi 30 giorni.
      </p>
      <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
        Registra le chiusure incollando l&apos;URL del pixel nel tuo CRM
        o automatizzando via POST. Il portal registra automaticamente{' '}
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
