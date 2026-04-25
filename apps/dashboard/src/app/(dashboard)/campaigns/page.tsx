/**
 * /campaigns — Acquisition campaigns management.
 *
 * An acquisition campaign bundles the five wizard module configs
 * (sorgente, tecnico, economico, outreach, crm) into a named, reusable
 * targeting strategy. Each tenant starts with one "Campagna Default"
 * pre-seeded from their wizard configuration.
 *
 * This page replaces the old "Campagne" send-history view.
 * Individual send history → /invii.
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';


import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { KpiChipCard } from '@/components/ui/kpi-chip-card';
import { listAcquisitionCampaigns } from '@/lib/data/acquisition-campaigns';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { cn, relativeTime } from '@/lib/utils';
import type { AcquisitionCampaignRow } from '@/types/db';

export const dynamic = 'force-dynamic';

export default async function CampaignsPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const campaigns = await listAcquisitionCampaigns();

  const active = campaigns.filter((c) => c.status === 'active').length;
  const paused = campaigns.filter((c) => c.status === 'paused').length;
  const draft = campaigns.filter((c) => c.status === 'draft').length;
  const archived = campaigns.filter((c) => c.status === 'archived').length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <header className="flex items-end justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Acquisizione · {campaigns.length} campagn{campaigns.length === 1 ? 'a' : 'e'}
          </p>
          <h1 className="font-headline text-4xl font-bold tracking-tighter">
            Campagne
          </h1>
          <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
            Strategie di acquisizione riutilizzabili — ogni campagna porta
            i parametri di targeting, il budget e gli inbox dedicati. Gli
            invii individuali sono visibili su{' '}
            <Link href={'/invii'} className="font-semibold text-primary hover:underline">
              Invii →
            </Link>
          </p>
        </div>
        <Link
          href="/campaigns/new"
          className="rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-on-primary shadow-ambient-sm hover:opacity-90"
        >
          + Nuova campagna
        </Link>
      </header>

      {/* KPI strip */}
      <BentoGrid cols={4}>
        <KpiChipCard label="Attive" value={String(active)} accent="primary" />
        <KpiChipCard
          label="In pausa"
          value={String(paused)}
          accent={paused > 0 ? 'tertiary' : 'neutral'}
        />
        <KpiChipCard label="Bozze" value={String(draft)} accent="neutral" />
        <KpiChipCard label="Archiviate" value={String(archived)} accent="secondary" />
      </BentoGrid>

      {/* Campaign list */}
      {campaigns.length === 0 ? (
        <BentoCard span="full">
          <div className="py-10 text-center">
            <p className="font-headline text-xl font-bold">
              Nessuna campagna configurata
            </p>
            <p className="mt-2 text-sm text-on-surface-variant">
              La campagna default viene creata automaticamente al primo onboarding.
              Se non è presente, completa la configurazione wizard in{' '}
              <Link href={'/settings/modules'} className="font-semibold text-primary hover:underline">
                Impostazioni → Moduli
              </Link>
              .
            </p>
          </div>
        </BentoCard>
      ) : (
        <div className="space-y-3">
          {campaigns.map((campaign) => (
            <CampaignCard key={campaign.id} campaign={campaign} />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Campaign card
// ---------------------------------------------------------------------------

function CampaignCard({ campaign }: { campaign: AcquisitionCampaignRow }) {
  const sorgente = campaign.sorgente_config as Record<string, unknown>;
  const atecoCodes = (sorgente?.ateco_codes as string[] | undefined) ?? [];
  const province = (sorgente?.province as string[] | undefined) ?? [];
  const regioni = (sorgente?.regioni as string[] | undefined) ?? [];
  const economico = campaign.economico_config as Record<string, unknown>;
  const budgetEur = economico?.budget_outreach_eur_month as number | undefined;

  const geo = [...regioni, ...province].slice(0, 3).join(', ') || 'Italia intera';
  const atecoPeek = atecoCodes.slice(0, 3).join(', ') || '—';

  return (
    <div className="rounded-xl border border-outline-variant/40 bg-surface-container-lowest px-5 py-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          {/* Title row */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-headline text-base font-bold tracking-tight text-on-surface">
              {campaign.name}
            </span>
            {campaign.is_default && (
              <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
                Default
              </span>
            )}
            <CampaignStatusChip status={campaign.status} />
          </div>

          {/* Description */}
          {campaign.description && (
            <p className="mt-1 text-sm text-on-surface-variant line-clamp-2">
              {campaign.description}
            </p>
          )}

          {/* Config summary row */}
          <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-on-surface-variant">
            <span>
              <span className="font-semibold text-on-surface">ATECO:</span>{' '}
              {atecoPeek}
              {atecoCodes.length > 3 ? ` +${atecoCodes.length - 3} altri` : ''}
            </span>
            <span>
              <span className="font-semibold text-on-surface">Geo:</span>{' '}
              {geo}
            </span>
            {budgetEur != null && (
              <span>
                <span className="font-semibold text-on-surface">Budget:</span>{' '}
                €{budgetEur.toLocaleString('it-IT')}/mese
              </span>
            )}
            {campaign.schedule_cron && (
              <span>
                <span className="font-semibold text-on-surface">Cron:</span>{' '}
                {campaign.schedule_cron}
              </span>
            )}
            <span className="ml-auto text-on-surface-variant">
              Aggiornata {relativeTime(campaign.updated_at)}
            </span>
          </div>
        </div>

        {/* Actions */}
        <div className="flex shrink-0 items-center gap-2">
          <Link
            href={`/invii?campaign=${campaign.id}`}
            className="rounded-lg border border-outline-variant/60 px-3 py-1.5 text-xs font-semibold text-on-surface hover:bg-surface-container-low"
          >
            Invii →
          </Link>
          <Link
            href={`/campaigns/${campaign.id}`}
            className="rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-on-primary shadow-ambient-sm hover:opacity-90"
          >
            Gestisci →
          </Link>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status chip
// ---------------------------------------------------------------------------

function CampaignStatusChip({ status }: { status: string }) {
  const styles: Record<string, string> = {
    active: 'bg-primary-container text-on-primary-container',
    draft: 'bg-surface-container-high text-on-surface-variant',
    paused: 'bg-tertiary-container text-on-tertiary-container',
    archived: 'bg-surface-container text-on-surface-variant opacity-60',
  };
  const labels: Record<string, string> = {
    active: 'Attiva',
    draft: 'Bozza',
    paused: 'In pausa',
    archived: 'Archiviata',
  };
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider',
        styles[status] ?? 'bg-surface-container text-on-surface-variant',
      )}
    >
      {labels[status] ?? status}
    </span>
  );
}
