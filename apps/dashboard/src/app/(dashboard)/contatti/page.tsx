/**
 * Contatti — companies that survived the v3 funnel and are ready for
 * outreach (`solar_verdict='accepted'`). Show the v3 enrichment data:
 * Places display name, predicted sector, parsed address, scraped email
 * and phone, Haiku score, building quality.
 *
 * Semantic distinction (v3):
 *   Contatto = azienda con tetto idoneo, pre-engagement
 *   Lead     = contatto che ha reagito (CTA click, portale, WhatsApp,
 *              reply email, appuntamento). Vivono in /leads.
 *
 * Rejected/skipped candidates are intentionally hidden from the table
 * — their counts are still shown in the sub-strip under the KPI cards.
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
} from '@/lib/data/contatti';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { formatNumber } from '@/lib/utils';

export const dynamic = 'force-dynamic';

type Search = Promise<{
  page?: string;
  territory_id?: string;
}>;

export default async function ContattiPage({
  searchParams,
}: {
  searchParams: Search;
}) {
  const sp = await searchParams;
  const page = Math.max(1, Number(sp.page) || 1);

  // The default filter (in lib/data/contatti.ts) restricts the list to
  // candidates with `solar_verdict='accepted'`. We don't expose stage or
  // verdict filter chips — operators care about qualified contacts only;
  // the L4 breakdown sub-strip below shows the rejected/skipped counts
  // for context. `territory_id` is still honored if passed via URL.
  const filter: ContattiFilter = {
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
            Contatti qualificati · {formatNumber(summary.l4_qualified)} con tetto idoneo
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
      {/* Funnel waterfall — riassunto sintetico delle fasi della scansione.
          La tabella sotto mostra solo l'ultimo stadio (Tetto idoneo); i
          conteggi qui aiutano a capire dove cadono i candidati durante il
          processo. */}
      <BentoGrid cols={4}>
        <KpiChipCard
          label="Scansionati"
          value={formatNumber(summary.l1)}
          hint="Aziende viste su Google Places"
          accent="neutral"
        />
        <KpiChipCard
          label="Con dati web"
          value={formatNumber(summary.l2)}
          hint={summary.l1 > 0 ? `${Math.round((summary.l2 / summary.l1) * 100)}% pass-through` : '—'}
          accent="primary"
        />
        <KpiChipCard
          label="Score AI"
          value={formatNumber(summary.l3)}
          hint={summary.l2 > 0 ? `${Math.round((summary.l3 / summary.l2) * 100)}% pass-through` : '—'}
          accent="tertiary"
        />
        <KpiChipCard
          label="Tetto idoneo"
          value={formatNumber(summary.l4_qualified)}
          hint={
            summary.l3 > 0
              ? `${Math.round((summary.l4_qualified / summary.l3) * 100)}% · mostrati in tabella`
              : 'mostrati in tabella'
          }
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

      {/* Table */}
      <BentoCard padding="tight" span="full">
        {rows.length === 0 ? (
          <div className="rounded-lg bg-surface-container-low p-12 text-center">
            {summary.l1 > 0 ? (
              <p className="text-sm text-on-surface-variant">
                Hai scansionato <strong>{formatNumber(summary.l1)}</strong> aziende
                ma nessuna ha ancora un tetto idoneo verificato. Aspetta che
                completi il funnel oppure ridimensiona il territorio.
              </p>
            ) : (
              <p className="text-sm text-on-surface-variant">
                Nessun contatto ancora.{' '}
                <Link
                  href="/territories"
                  className="font-semibold text-primary hover:underline"
                >
                  Avvia una scansione
                </Link>{' '}
                per popolare la lista.
              </p>
            )}
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

