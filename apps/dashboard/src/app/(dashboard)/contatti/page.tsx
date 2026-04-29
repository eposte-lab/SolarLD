/**
 * Contatti — raw scan_candidates discovered by the B2B funnel.
 *
 * These are companies sourced by Atoka (L1) and progressively enriched
 * through L2-L4. They are NOT yet in the sales pipeline — only those
 * promoted by the ScoringAgent appear in /leads.
 *
 * Distinction:
 *   Contatto = azienda scoperta durante scan, mai contattata
 *   Lead     = contatto qualificato che ha ricevuto almeno un outreach
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { GradientButton } from '@/components/ui/gradient-button';
import { KpiChipCard } from '@/components/ui/kpi-chip-card';
import { ContattiTable } from '@/components/contatti/contatti-table';
import {
  CONTATTI_PAGE_SIZE,
  getContattiSummary,
  listContatti,
  type ContattiFilter,
  type SolarVerdict,
} from '@/lib/data/contatti';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { cn, formatNumber } from '@/lib/utils';

export const dynamic = 'force-dynamic';

type Search = Promise<{
  page?: string;
  stage?: string;
  territory_id?: string;
}>;

const STAGE_LABELS: Record<number, string> = {
  1: 'Scoperto (L1)',
  2: 'Arricchito (L2)',
  3: 'Punteggio assegnato (L3)',
  4: 'Tetto idoneo (L4)',
};

const STAGE_FILTER_OPTIONS = [
  { value: '', label: 'Tutti' },
  { value: '1', label: 'L1 — Scoperto' },
  { value: '2', label: 'L2 — Arricchito' },
  { value: '3', label: 'L3 — Punteggio' },
  { value: '4', label: 'L4 — Tetto idoneo' },
];

export default async function ContattiPage({
  searchParams,
}: {
  searchParams: Search;
}) {
  const sp = await searchParams;
  const page = Math.max(1, Number(sp.page) || 1);
  const stageNum = sp.stage ? Number(sp.stage) : undefined;

  const filter: ContattiFilter = {
    stage: stageNum && stageNum >= 1 && stageNum <= 4 ? stageNum : undefined,
    territory_id: sp.territory_id || undefined,
  };

  const [ctx, summary, { rows, total }] = await Promise.all([
    getCurrentTenantContext(),
    getContattiSummary(),
    listContatti({ page, filter }),
  ]);
  if (!ctx) redirect('/login');

  const totalPages = Math.max(1, Math.ceil(total / CONTATTI_PAGE_SIZE));

  const queryFor = (overrides: Record<string, string | undefined>) => {
    const params = new URLSearchParams();
    if (filter.stage) params.set('stage', String(filter.stage));
    if (filter.territory_id) params.set('territory_id', filter.territory_id);
    if (page > 1) params.set('page', String(page));
    for (const [k, v] of Object.entries(overrides)) {
      if (v === undefined || v === '') params.delete(k);
      else params.set(k, v);
    }
    const s = params.toString();
    return s ? `/contatti?${s}` : '/contatti';
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <header className="flex items-end justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Top-of-funnel · {formatNumber(summary.total)} contatti totali
          </p>
          <h1 className="font-headline text-4xl font-bold tracking-tighter">
            Contatti
          </h1>
        </div>
        <GradientButton href="/territories" size="sm" variant="secondary">
          + Territorio
        </GradientButton>
      </header>

      {/* Funnel summary KPI strip */}
      <BentoGrid cols={4}>
        <KpiChipCard
          label="Scoperte (L1)"
          value={formatNumber(summary.l1)}
          hint="Aziende scoperte"
          accent="neutral"
        />
        <KpiChipCard
          label="Arricchite (L2)"
          value={formatNumber(summary.l2)}
          hint={summary.l1 > 0 ? `${Math.round((summary.l2 / summary.l1) * 100)}% pass-through` : '—'}
          accent="primary"
        />
        <KpiChipCard
          label="Scored (L3)"
          value={formatNumber(summary.l3)}
          hint={summary.l2 > 0 ? `${Math.round((summary.l3 / summary.l2) * 100)}% pass-through` : '—'}
          accent="tertiary"
        />
        <KpiChipCard
          label="Qualificate (L4)"
          value={formatNumber(summary.l4_qualified)}
          hint={summary.l3 > 0 ? `${Math.round((summary.l4_qualified / summary.l3) * 100)}% pass-through` : '—'}
          accent="secondary"
        />
      </BentoGrid>

      {/* L4 breakdown sub-strip */}
      {(summary.l4_rejected > 0 || summary.l4_skipped > 0) && (
        <div className="flex flex-wrap gap-3">
          <div className="rounded-lg bg-surface-container-low px-4 py-2">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
              L4 Rifiutate (tecnico)
            </span>
            <span className="ml-3 font-headline font-bold text-on-surface-variant">
              {formatNumber(summary.l4_rejected)}
            </span>
          </div>
          <div className="rounded-lg bg-surface-container-low px-4 py-2">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
              L4 Skip (gate score)
            </span>
            <span className="ml-3 font-headline font-bold text-on-surface-variant">
              {formatNumber(summary.l4_skipped)}
            </span>
          </div>
          <div className="rounded-lg bg-surface-container-low px-4 py-2">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Nessun edificio
            </span>
            <span className="ml-3 font-headline font-bold text-on-surface-variant">
              {formatNumber(summary.l4_no_building)}
            </span>
          </div>
        </div>
      )}

      {/* Filters */}
      <BentoCard padding="tight" span="full">
        <div className="flex flex-wrap items-center gap-6 px-2 py-2">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Stadio
            </span>
            <div className="flex flex-wrap gap-1.5">
              {STAGE_FILTER_OPTIONS.map((opt) => (
                <FilterChip
                  key={opt.value || 'all'}
                  active={String(filter.stage ?? '') === opt.value}
                  href={queryFor({
                    stage: opt.value || undefined,
                    page: undefined,
                  })}
                >
                  {opt.label}
                </FilterChip>
              ))}
            </div>
          </div>
        </div>
      </BentoCard>

      {/* Table */}
      <BentoCard padding="tight" span="full">
        {rows.length === 0 ? (
          <div className="rounded-lg bg-surface-container-low p-12 text-center">
            <p className="text-sm text-on-surface-variant">
              Nessun contatto trovato.{' '}
              <Link
                href="/territories"
                className="font-semibold text-primary hover:underline"
              >
                Avvia una scansione
              </Link>{' '}
              per popolare la lista.
            </p>
          </div>
        ) : (
          <ContattiTable rows={rows} />
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="mt-4 flex items-center justify-between px-2">
            <span className="text-xs text-on-surface-variant">
              Pagina {page} di {totalPages} · {formatNumber(total)} totali
            </span>
            <div className="flex gap-2">
              {page > 1 && (
                <GradientButton
                  href={queryFor({ page: String(page - 1) })}
                  variant="secondary"
                  size="sm"
                >
                  ← Precedente
                </GradientButton>
              )}
              {page < totalPages && (
                <GradientButton
                  href={queryFor({ page: String(page + 1) })}
                  variant="secondary"
                  size="sm"
                >
                  Successiva →
                </GradientButton>
              )}
            </div>
          </div>
        )}
      </BentoCard>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Local helpers
// ---------------------------------------------------------------------------

function FilterChip({
  active,
  href,
  children,
}: {
  active: boolean;
  href: string;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className={cn(
        'rounded-full px-3 py-1 text-xs font-semibold transition-colors',
        active
          ? 'bg-primary text-on-primary shadow-ambient-sm'
          : 'bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface',
      )}
    >
      {children}
    </Link>
  );
}
