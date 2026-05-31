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
import { DemoModeBanner } from '@/components/demo-mode-banner';
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
  scartati?: string;
}>;

export default async function ContattiPage({
  searchParams,
}: {
  searchParams: Search;
}) {
  const sp = await searchParams;
  const page = Math.max(1, Number(sp.page) || 1);
  const includeScartati = sp.scartati === '1';

  // Default filter: only candidates that passed Solar API AND were promoted
  // to a `leads` row (the post-L6 "perfect" contacts). When the operator
  // toggles `?scartati=1`, the table also shows pre-promotion scan_candidates
  // (mid-funnel rows + Solar-rejected) for debug.
  const filter: ContattiFilter = {
    territory_id: sp.territory_id || undefined,
    include_unpromoted: includeScartati,
  };

  // A moderated trial tenant SEES its contatti — the moderation gate is on
  // the contatto → lead state promotion (in lib/data/leads.ts), not on this
  // page. /contatti reads scan_candidates, which is never gated.
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
    if (includeScartati) params.set('scartati', '1');
    for (const [k, v] of Object.entries(overrides)) {
      if (v === undefined || v === '') params.delete(k);
      else params.set(k, v);
    }
    const s = params.toString();
    return s ? `/contatti?${s}` : '/contatti';
  };

  return (
    <div className="space-y-4">
      {ctx.tenant.outreach_blocked && (
        <DemoModeBanner tenantName={ctx.tenant.business_name ?? null} />
      )}

      {/* Header */}
      <header className="flex items-end justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Contatti qualificati · {formatNumber(summary.l4_qualified)} con tetto idoneo
          </p>
          <h1 className="font-headline text-2xl font-bold tracking-tighter md:text-4xl">
            Contatti
          </h1>
        </div>
        <GradientButton href="/territorio" size="sm" variant="secondary">
          + Territorio
        </GradientButton>
      </header>

      {/* KPI strip — qualitative aggregates over the qualified set.
          Replaces the old 3-stage waterfall (Scansionati/Con dati web/
          Score AI) which always read 100/100/100% pass-through under
          v3 (the gates that filter candidates live at L4 Solar, not at
          L1-L3). New strip surfaces info that actually moves with the
          data: total installable capacity, avg AI score, contactable
          email count. */}
      <BentoGrid cols={4}>
        <KpiChipCard
          label="Convalidati"
          value={formatNumber(summary.l4_qualified)}
          hint="tetto idoneo · in tabella"
          accent="primary"
        />
        <KpiChipCard
          label="kW installabili"
          value={formatNumber(summary.total_kwp_installable)}
          hint="potenza totale stimata"
          accent="tertiary"
        />
        <KpiChipCard
          label="Score AI medio"
          value={
            summary.avg_overall_score != null
              ? formatNumber(summary.avg_overall_score)
              : '—'
          }
          hint="qualità media batch"
          tone="neutral"
        />
        <KpiChipCard
          label="Email valida"
          value={formatNumber(summary.valid_email_count)}
          hint={
            summary.l4_qualified > 0
              ? `${Math.round((summary.valid_email_count / summary.l4_qualified) * 100)}% sui convalidati`
              : '—'
          }
          accent="neutral"
        />
      </BentoGrid>

      {/* Toggle "mostra anche scartati" — debug-only, off by default. */}
      <div className="flex items-center justify-end">
        <Link
          href={queryFor({ scartati: includeScartati ? undefined : '1', page: undefined })}
          className="text-xs font-semibold uppercase tracking-widest text-on-surface-variant hover:text-on-surface"
        >
          {includeScartati ? '◉ Mostra solo convalidati' : '○ Mostra anche scartati'}
        </Link>
      </div>

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
                  href="/territorio"
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

