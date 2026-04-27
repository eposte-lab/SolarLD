/**
 * /campaigns/[id] — Campaign detail hub.
 *
 * Four tabs (driven by ?tab= search param to stay server-rendered):
 *   config    — Edit the 5 module configs for this campaign snapshot.
 *   override  — Time-boxed patches applied on top of the base config.
 *   risultati — Aggregated send performance per variant / zone / segment.
 *   (Default: config)
 *
 * All heavy data loads are server-side; interactive mutations (save config,
 * create/delete override) are handled by client components.
 */

import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';

import { CampaignConfigEditor } from '@/components/campaigns/CampaignConfigEditor';
import { CampaignOverrideList } from '@/components/campaigns/CampaignOverrideList';
import { DailyCapWidget } from '@/components/dashboard/daily-cap-widget';
import { BadgeStatus } from '@/components/ui/badge-status';
import { BentoCard } from '@/components/ui/bento-card';
import { KpiChipCard } from '@/components/ui/kpi-chip-card';
import { SectionEyebrow } from '@/components/ui/section-eyebrow';
import { getAcquisitionCampaign, getCampaignSendStats } from '@/lib/data/acquisition-campaigns';
import { getCampaignResults } from '@/lib/data/campaign-results';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { getDailyCapStats } from '@/lib/data/usage';
import { cn, relativeTime } from '@/lib/utils';

import { updateCampaignStatus } from '../_actions';

export const dynamic = 'force-dynamic';

const TABS = ['config', 'override', 'risultati'] as const;
type Tab = (typeof TABS)[number];

const TAB_LABELS: Record<Tab, string> = {
  config: 'Configurazione',
  override: 'Override',
  risultati: 'Risultati',
};

export default async function CampaignDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ tab?: string; error?: string }>;
}) {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const { id } = await params;
  const { tab: rawTab, error } = await searchParams;
  const tab: Tab = (TABS as readonly string[]).includes(rawTab ?? '')
    ? (rawTab as Tab)
    : 'config';

  const campaign = await getAcquisitionCampaign(id);
  if (!campaign) notFound();

  const territoryLocked = Boolean(
    (ctx.tenant as unknown as { territory_locked_at?: string | null })
      .territory_locked_at,
  );

  // Parallel data loads for each tab.
  const [stats, results, overrides, dailyCap] = await Promise.all([
    getCampaignSendStats(id),
    tab === 'risultati' ? getCampaignResults(id, ctx.tenant.id) : null,
    (() => {
      // Inline import for campaign-overrides server-side reads via Supabase.
      // We call the Supabase client directly here (server component) rather than
      // the API (which would require a session token in the server context).
      return import('@/lib/supabase/server').then(async ({ createSupabaseServerClient }) => {
        const sb = await createSupabaseServerClient();
        const { data } = await sb
          .from('campaign_overrides')
          .select('id, campaign_id, tenant_id, label, override_type, start_at, end_at, patch, experiment_id, created_at, created_by')
          .eq('campaign_id', id)
          .eq('tenant_id', ctx.tenant.id)
          .order('start_at', { ascending: false })
          .limit(100);
        return data ?? [];
      });
    })(),
    getDailyCapStats(),
  ]);

  const statusToBadge: Record<string, { tone: 'success' | 'warning' | 'neutral'; label: string }> = {
    active: { tone: 'success', label: 'Attiva' },
    draft: { tone: 'neutral', label: 'Bozza' },
    paused: { tone: 'warning', label: 'In pausa' },
    archived: { tone: 'neutral', label: 'Archiviata' },
  };
  const statusBadge = statusToBadge[campaign.status] ?? { tone: 'neutral' as const, label: campaign.status };

  return (
    <div className="space-y-6">
      {/* Header */}
      <header className="space-y-2">
        <SectionEyebrow>
          <Link href="/campaigns" className="hover:underline">
            Campagne
          </Link>
          {' · '}{campaign.name}
        </SectionEyebrow>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-headline text-4xl font-bold tracking-tightest text-on-surface">
                {campaign.name}
              </h1>
              {campaign.is_default && (
                <BadgeStatus tone="neutral" label="Default" dotless />
              )}
              <BadgeStatus tone={statusBadge.tone} label={statusBadge.label} />
            </div>
            {campaign.description && (
              <p className="max-w-xl text-sm text-on-surface-variant">
                {campaign.description}
              </p>
            )}
            <p className="text-xs text-on-surface-variant/60">
              Aggiornata {relativeTime(campaign.updated_at)}
            </p>
          </div>

          {/* Status actions */}
          <div className="flex gap-2">
            {campaign.status !== 'active' && campaign.status !== 'archived' && (
              <form action={updateCampaignStatus}>
                <input type="hidden" name="campaign_id" value={campaign.id} />
                <input type="hidden" name="action" value="activate" />
                <button
                  type="submit"
                  className="rounded-xl bg-primary px-3 py-1.5 text-xs font-semibold text-on-primary shadow-editorial-glow transition-transform hover:-translate-y-0.5"
                >
                  Attiva
                </button>
              </form>
            )}
            {campaign.status === 'active' && (
              <form action={updateCampaignStatus}>
                <input type="hidden" name="campaign_id" value={campaign.id} />
                <input type="hidden" name="action" value="pause" />
                <button
                  type="submit"
                  className="rounded-xl ghost-border bg-surface-container-lowest px-3 py-1.5 text-xs font-semibold text-on-surface hover:bg-white/5"
                >
                  Metti in pausa
                </button>
              </form>
            )}
            {!campaign.is_default && campaign.status !== 'archived' && (
              <form action={updateCampaignStatus}>
                <input type="hidden" name="campaign_id" value={campaign.id} />
                <input type="hidden" name="action" value="archive" />
                <button
                  type="submit"
                  className="rounded-xl ghost-border bg-surface-container-lowest px-3 py-1.5 text-xs font-semibold text-on-surface-variant hover:bg-white/5 hover:text-on-surface"
                >
                  Archivia
                </button>
              </form>
            )}
          </div>
        </div>
      </header>

      {/* Error banner */}
      {error && (
        <div
          role="alert"
          className="rounded-xl bg-error-container px-4 py-3 text-sm font-semibold text-on-error-container"
        >
          {error === 'status_change_failed'
            ? 'Impossibile cambiare lo stato. Riprova.'
            : `Errore: ${error}`}
        </div>
      )}

      {/* Daily cap widget — compact */}
      <DailyCapWidget stats={dailyCap} compact />

      {/* KPI strip */}
      <div className="grid grid-cols-4 gap-3">
        <KpiChipCard label="Inviati" value={String(stats.total)} tone="highlight" />
        <KpiChipCard label="Consegnati" value={String(stats.delivered)} tone="success" />
        <KpiChipCard label="Falliti" value={String(stats.failed)} tone={stats.failed > 0 ? 'critical' : 'neutral'} />
        <KpiChipCard label="Override attivi" value={String((overrides as unknown[]).filter((o) => {
          const row = o as { start_at: string; end_at: string };
          const now = Date.now();
          return new Date(row.start_at).getTime() <= now && new Date(row.end_at).getTime() >= now;
        }).length)} tone="neutral" />
      </div>

      {/* Tab bar — single active pill, ghost border, no shadow swap */}
      <div className="flex gap-1 rounded-2xl ghost-border bg-surface-container-lowest p-1">
        {TABS.map((t) => (
          <Link
            key={t}
            href={`/campaigns/${id}?tab=${t}`}
            className={cn(
              'flex-1 rounded-xl px-3 py-2 text-center text-sm font-semibold transition-colors',
              tab === t
                ? 'bg-primary/10 text-primary'
                : 'text-on-surface-variant hover:bg-white/5 hover:text-on-surface',
            )}
          >
            {TAB_LABELS[t]}
          </Link>
        ))}
      </div>

      {/* Tab content */}
      {tab === 'config' && (
        <BentoCard span="full">
          <div className="p-1">
            <p className="mb-4 text-xs text-on-surface-variant">
              Questi parametri sono la <strong>snapshot della campagna</strong> —
              separati dalla configurazione globale in Impostazioni → Moduli.
              Le modifiche qui non influenzano altre campagne.
            </p>
            <CampaignConfigEditor
              campaign={campaign}
              territoryLocked={territoryLocked}
            />
          </div>
        </BentoCard>
      )}

      {tab === 'override' && (
        <BentoCard span="full">
          <div className="space-y-2 p-1">
            <div className="space-y-1">
              <h2 className="font-headline text-lg font-bold text-on-surface">
                Override temporanei
              </h2>
              <p className="text-xs text-on-surface-variant">
                Un override applica un patch JSON sulla configurazione base
                per la finestra temporale specificata. Alla scadenza la
                campagna torna automaticamente alla configurazione base.
              </p>
            </div>
            <CampaignOverrideList
              campaignId={campaign.id}
              // biome-ignore lint/suspicious/noExplicitAny: typed by CampaignOverrideRow
              initialOverrides={overrides as any}
            />
          </div>
        </BentoCard>
      )}

      {tab === 'risultati' && (
        <div className="space-y-6">
          {results && results.length > 0 && <ResultsChart rows={results} />}
          <BentoCard span="full">
            <div className="space-y-4 p-1">
              <div className="space-y-1">
                <SectionEyebrow>Risultati per variante · 30gg</SectionEyebrow>
                <h2 className="font-headline text-2xl font-bold tracking-tighter text-on-surface">
                  Performance breakdown
                </h2>
              </div>
              {!results || results.length === 0 ? (
                <p className="py-8 text-center text-sm text-on-surface-variant">
                  Nessun invio ancora — i risultati appariranno non appena la
                  campagna inizia a inviare.
                </p>
              ) : (
                <ResultsTable rows={results} />
              )}
            </div>
          </BentoCard>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Results table
// ---------------------------------------------------------------------------

import { EditorialLineChart } from '@/components/charts/editorial-line-chart';
import type { CampaignResultRow } from '@/lib/data/campaign-results';

/**
 * ResultsChart — Editorial line chart top-of-page nei risultati campagna.
 *
 * Aggrega per variante (max 4): linea bianca per "Inviati", linea amber
 * focused per "Aperti" (la metrica che il marketer guarda di più). Le
 * etichette inline + reference lines a 25/50/75% sostituiscono il tooltip.
 */
function ResultsChart({ rows }: { rows: CampaignResultRow[] }) {
  // Aggrega per variante per dare una serie tabellare (Recharts data shape)
  const byVariant = new Map<string, { sent: number; opened: number; clicked: number }>();
  for (const r of rows) {
    const key = r.variant ?? 'Controllo';
    const cur = byVariant.get(key) ?? { sent: 0, opened: 0, clicked: 0 };
    cur.sent += r.sent;
    cur.opened += r.opened;
    cur.clicked += r.clicked;
    byVariant.set(key, cur);
  }
  const data = Array.from(byVariant.entries()).map(([variant, v]) => ({
    variant,
    sent: v.sent,
    opened: v.opened,
    clicked: v.clicked,
  }));

  if (data.length < 2) return null; // serve almeno 2 punti per una line

  const totalSent = data.reduce((s, d) => s + d.sent, 0);
  const totalOpened = data.reduce((s, d) => s + d.opened, 0);
  const openRate = totalSent > 0 ? Math.round((totalOpened / totalSent) * 100) : 0;

  return (
    <BentoCard span="full" variant="glass">
      <div className="mb-4 flex items-end justify-between">
        <div className="space-y-1">
          <SectionEyebrow tone="mint">Open rate trend per variante</SectionEyebrow>
          <p className="font-headline text-3xl font-bold tabular-nums tracking-tightest text-on-surface">
            {totalOpened}
            <span className="hero-decimal text-base"> / {totalSent} aperti</span>
          </p>
        </div>
      </div>
      <EditorialLineChart
        data={data}
        xKey="variant"
        height={220}
        series={[
          { key: 'sent', label: 'Inviati', color: 'whiteDim' },
          { key: 'opened', label: 'Aperti', color: 'mint', focused: true },
        ]}
        yReferenceLines={[Math.max(...data.map((d) => d.sent)) * 0.25, Math.max(...data.map((d) => d.sent)) * 0.5, Math.max(...data.map((d) => d.sent)) * 0.75]}
        yReferenceLabels={['25%', '50%', '75%']}
        inlineLabel={{ value: `${totalOpened}`, delta: `${openRate}%` }}
      />
    </BentoCard>
  );
}

function ResultsTable({ rows }: { rows: CampaignResultRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/8 text-left">
            {[
              'Variante',
              'Provincia',
              'Tier',
              'Inviati',
              'Consegnati',
              'Aperti',
              'Cliccati',
              'Risposte',
              'Open%',
            ].map((h) => (
              <th
                key={h}
                className="pb-2 pr-4 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {rows.map((row, i) => {
            const openRate = row.sent > 0 ? ((row.opened / row.sent) * 100).toFixed(1) : '—';
            return (
              <tr key={i} className="hover:bg-white/3">
                <td className="py-2 pr-4 font-medium text-on-surface">
                  {row.variant ?? 'Controllo'}
                </td>
                <td className="py-2 pr-4 text-on-surface-variant">
                  {row.province ?? '—'}
                </td>
                <td className="py-2 pr-4 text-on-surface-variant">
                  {row.score_tier ?? '—'}
                </td>
                <td className="py-2 pr-4 tabular-nums">{row.sent}</td>
                <td className="py-2 pr-4 tabular-nums text-on-surface-variant">
                  {row.delivered}
                </td>
                <td className="py-2 pr-4 tabular-nums text-on-surface-variant">
                  {row.opened}
                </td>
                <td className="py-2 pr-4 tabular-nums text-on-surface-variant">
                  {row.clicked}
                </td>
                <td className="py-2 pr-4 tabular-nums text-on-surface-variant">
                  {row.replied}
                </td>
                <td
                  className={cn(
                    'py-2 pr-4 tabular-nums font-semibold',
                    row.opened / row.sent > 0.15
                      ? 'text-primary'
                      : 'text-on-surface-variant',
                  )}
                >
                  {openRate}
                  {openRate !== '—' ? '%' : ''}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
