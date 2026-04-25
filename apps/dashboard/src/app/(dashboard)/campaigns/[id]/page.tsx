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
import { BentoCard } from '@/components/ui/bento-card';
import { KpiChipCard } from '@/components/ui/kpi-chip-card';
import { getAcquisitionCampaign, getCampaignSendStats } from '@/lib/data/acquisition-campaigns';
import { getCampaignResults } from '@/lib/data/campaign-results';
import { getCurrentTenantContext } from '@/lib/data/tenant';
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
  const [stats, results, overrides] = await Promise.all([
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
  ]);

  const statusColors: Record<string, string> = {
    active: 'bg-primary-container text-on-primary-container',
    draft: 'bg-surface-container-high text-on-surface-variant',
    paused: 'bg-tertiary-container text-on-tertiary-container',
    archived: 'bg-surface-container text-on-surface-variant opacity-60',
  };
  const statusLabels: Record<string, string> = {
    active: 'Attiva',
    draft: 'Bozza',
    paused: 'In pausa',
    archived: 'Archiviata',
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <header className="space-y-2">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          <Link href="/campaigns" className="hover:underline">
            Campagne
          </Link>
          {' · '}{campaign.name}
        </p>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-headline text-3xl font-bold tracking-tighter">
                {campaign.name}
              </h1>
              {campaign.is_default && (
                <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
                  Default
                </span>
              )}
              <span
                className={cn(
                  'rounded-full px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider',
                  statusColors[campaign.status] ?? 'bg-surface-container text-on-surface-variant',
                )}
              >
                {statusLabels[campaign.status] ?? campaign.status}
              </span>
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
                  className="rounded-xl bg-primary px-3 py-1.5 text-xs font-semibold text-on-primary shadow-ambient-sm"
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
                  className="rounded-xl border border-outline-variant/60 px-3 py-1.5 text-xs font-semibold text-on-surface hover:bg-surface-container-low"
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
                  className="rounded-xl border border-outline-variant/40 px-3 py-1.5 text-xs font-semibold text-on-surface-variant hover:bg-surface-container-low"
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

      {/* KPI strip */}
      <div className="grid grid-cols-4 gap-3">
        <KpiChipCard label="Inviati" value={String(stats.total)} accent="primary" />
        <KpiChipCard label="Consegnati" value={String(stats.delivered)} accent="secondary" />
        <KpiChipCard label="Falliti" value={String(stats.failed)} accent={stats.failed > 0 ? 'tertiary' : 'neutral'} />
        <KpiChipCard label="Override attivi" value={String((overrides as unknown[]).filter((o) => {
          const row = o as { start_at: string; end_at: string };
          const now = Date.now();
          return new Date(row.start_at).getTime() <= now && new Date(row.end_at).getTime() >= now;
        }).length)} accent="neutral" />
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 rounded-xl bg-surface-container-low p-1">
        {TABS.map((t) => (
          <Link
            key={t}
            href={`/campaigns/${id}?tab=${t}`}
            className={cn(
              'flex-1 rounded-lg px-3 py-2 text-center text-sm font-semibold transition-colors',
              tab === t
                ? 'bg-surface text-on-surface shadow-ambient-sm'
                : 'text-on-surface-variant hover:bg-surface-container-high',
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
        <BentoCard span="full">
          <div className="space-y-4 p-1">
            <h2 className="font-headline text-lg font-bold text-on-surface">
              Risultati per variante
            </h2>
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
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Results table
// ---------------------------------------------------------------------------

import type { CampaignResultRow } from '@/lib/data/campaign-results';

function ResultsTable({ rows }: { rows: CampaignResultRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-outline-variant/40 text-left">
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
                className="pb-2 pr-4 text-xs font-semibold text-on-surface-variant"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-outline-variant/20">
          {rows.map((row, i) => {
            const openRate = row.sent > 0 ? ((row.opened / row.sent) * 100).toFixed(1) : '—';
            return (
              <tr key={i} className="hover:bg-surface-container-low/50">
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
