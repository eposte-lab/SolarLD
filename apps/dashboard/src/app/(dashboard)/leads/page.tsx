/**
 * Leads list — Luminous Curator restyle (Fase B).
 *
 * Layout:
 *   - Editorial header with micro-label + total count
 *   - Filter bento card (tier chips + status chips, no 1px borders)
 *   - Table bento card — rows separated by ghost-border inset shadow
 *
 * Behavior is unchanged: query-param filters, server-rendered, zero
 * client state. Only the visual chrome is swapped for the new
 * primitives (BentoCard, StatusChip, TierChip, GradientButton).
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { BentoCard } from '@/components/ui/bento-card';
import { GradientButton } from '@/components/ui/gradient-button';
import { LeadsTable } from '@/components/leads/leads-table';
import {
  LEADS_PAGE_SIZE,
  listHotLeadsAwaitingResponse,
  listLeads,
  type LeadListFilter,
} from '@/lib/data/leads';
import { getContattiSummary } from '@/lib/data/contatti';
import { listTodayPredictionsByLead } from '@/lib/data/imminence';
import { getModuleForTenant } from '@/lib/data/modules.server';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import type { CRMConfig } from '@/types/modules';
import { cn, formatNumber } from '@/lib/utils';
import type { LeadScoreTier, LeadStatus } from '@/types/db';

export const dynamic = 'force-dynamic';

type Search = Promise<{
  page?: string;
  tier?: string;
  status?: string;
  q?: string;
  mode?: string;
  // Activity filters: '1' = require, undefined = any.
  read?: string;
  portal?: string;
  // Management mode: 'manual' (hot, system stopped) | 'auto' | undefined.
  gestione?: string;
  // Origin territory (tenant_target_areas.id) — driven by the chip on
  // /leads/[id]. When set, lists only leads sourced from that OSM zone.
  territorio?: string;
}>;

function parseTriState(raw: string | undefined): boolean | null | undefined {
  if (raw === '1') return true;
  if (raw === '0') return false;
  return undefined;
}

const TIERS: { value: '' | LeadScoreTier; label: string }[] = [
  { value: '', label: 'Tutti' },
  { value: 'hot', label: 'Hot' },
  { value: 'warm', label: 'Warm' },
  { value: 'cold', label: 'Cold' },
];

// Pre-engagement statuses (new / sent / delivered / opened) are
// intentionally absent: those rows live in /contatti and never appear
// here because `listLeads()` applies an engagement gate.
const STATUSES: { value: '' | LeadStatus; label: string }[] = [
  { value: '', label: 'Tutti' },
  { value: 'clicked', label: 'Click' },
  { value: 'engaged', label: 'Engaged' },
  { value: 'whatsapp', label: 'WhatsApp' },
  { value: 'appointment', label: 'Ha richiesto contatto' },
  { value: 'closed_won', label: 'Chiuso (win)' },
  { value: 'closed_lost', label: 'Chiuso (perso)' },
];

export default async function LeadsPage({ searchParams }: { searchParams: Search }) {
  // searchParams is a fast Promise (URL params only — no I/O), so await it
  // first then fire ctx + data in parallel.
  const sp = await searchParams;
  const page = Math.max(1, Number(sp.page) || 1);
  const isHotMode = sp.mode === 'hot';
  const filter: LeadListFilter = {
    tier: (sp.tier as LeadScoreTier) || undefined,
    status: (sp.status as LeadStatus) || undefined,
    q: sp.q || undefined,
    read: parseTriState(sp.read),
    portalVisited: parseTriState(sp.portal),
    management:
      sp.gestione === 'manual' || sp.gestione === 'auto'
        ? sp.gestione
        : undefined,
    territoryId: sp.territorio || undefined,
  };

  // In "Caldi adesso" mode we ignore the tier/status filters because
  // they would conflict with the operational definition (engagement
  // ≥ 60, recent portal event, NOT in a closing pipeline stage).
  const [ctx, listResult, hotRows, contattiSummary, predictionsByLead] =
    await Promise.all([
      getCurrentTenantContext(),
      isHotMode
        ? Promise.resolve({ rows: [], total: 0 })
        : listLeads({ page, filter }),
      isHotMode
        ? listHotLeadsAwaitingResponse({ sinceHours: 72, limit: 50 })
        : Promise.resolve([]),
      getContattiSummary(),
      listTodayPredictionsByLead(),
    ]);
  if (!ctx) redirect('/login');
  const { rows: regularRows, total } = listResult;
  const rows = isHotMode ? hotRows : regularRows;
  const effectiveTotal = isHotMode ? hotRows.length : total;
  const totalPages = isHotMode
    ? 1
    : Math.max(1, Math.ceil(total / LEADS_PAGE_SIZE));

  // Load the tenant's CRM module to get their custom pipeline labels.
  // Runs after ctx (needs tenant id) but is non-blocking for the table.
  const crmModule = await getModuleForTenant(ctx.tenant.id, 'crm');
  const pipelineLabels = (crmModule.config as CRMConfig).pipeline_labels;

  const queryFor = (overrides: Record<string, string | undefined>) => {
    const params = new URLSearchParams();
    if (isHotMode) params.set('mode', 'hot');
    if (filter.tier) params.set('tier', filter.tier);
    if (filter.status) params.set('status', filter.status);
    if (filter.q) params.set('q', filter.q);
    if (sp.read !== undefined) params.set('read', sp.read);
    if (sp.portal !== undefined) params.set('portal', sp.portal);
    if (sp.gestione !== undefined) params.set('gestione', sp.gestione);
    if (sp.territorio !== undefined) params.set('territorio', sp.territorio);
    if (page > 1) params.set('page', String(page));
    for (const [k, v] of Object.entries(overrides)) {
      if (v === undefined || v === '') params.delete(k);
      else params.set(k, v);
    }
    const s = params.toString();
    return s ? `/leads?${s}` : '/leads';
  };

  return (
    <div className="space-y-6">
      {/* Header ------------------------------------------------------- */}
      <header className="flex items-end justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            {isHotMode
              ? `Caldi adesso · ${effectiveTotal.toLocaleString('it-IT')} da chiamare`
              : `Contatti che hanno reagito · ${effectiveTotal.toLocaleString('it-IT')} lead engagati`}
          </p>
          <h1 className="font-headline text-4xl font-bold tracking-tighter">
            {isHotMode ? 'Caldi adesso' : 'Lead Attivi'}
          </h1>
        </div>
      </header>


      {/* Mode tabs ---------------------------------------------------- */}
      <div className="flex gap-2">
        <Link
          href="/leads"
          className={cn(
            'rounded-full px-4 py-1.5 text-xs font-semibold transition-colors',
            !isHotMode
              ? 'bg-primary text-on-primary shadow-ambient-sm'
              : 'bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface',
          )}
        >
          Tutti i lead
        </Link>
        <Link
          href="/leads?mode=hot"
          className={cn(
            'rounded-full px-4 py-1.5 text-xs font-semibold transition-colors',
            isHotMode
              ? 'bg-primary text-on-primary shadow-ambient-sm'
              : 'bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface',
          )}
        >
          Caldi adesso
        </Link>
      </div>

      {isHotMode && (
        <div className="rounded-xl bg-surface-container-low px-5 py-3 text-xs text-on-surface-variant">
          Lead con almeno un&apos;attività sul portale nelle ultime 72h, non
          ancora in conversazione attiva (WhatsApp / appuntamento / chiusi).
          Ordinati per engagement e ultimo evento.
        </div>
      )}

      {/* Contatti in attesa banner — solo se ci sono qualificati ma
          nessun lead engagato (o pochi). Aiuta l'operatore a capire
          dove sono "i lead" quando la lista appare vuota. */}
      {contattiSummary.l4_qualified > 0 && !isHotMode && (
        <div className="flex items-center justify-between rounded-xl bg-surface-container-low px-5 py-3">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-tertiary-container text-on-tertiary-container">
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor">
                <path d="M15 12a3 3 0 100-6 3 3 0 000 6zm-9-1a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM15 14c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4zm-9 1c-.29 0-.62.02-.97.05C6.19 15.93 7 16.8 7 18v2H0v-2c0-2.21 2.69-3.4 6-3.95z" />
              </svg>
            </div>
            <div>
              <p className="text-sm font-semibold text-on-surface">
                {formatNumber(contattiSummary.l4_qualified)} contatti qualificati in attesa di reazione
              </p>
              <p className="text-xs text-on-surface-variant">
                Diventano lead quando cliccano CTA, visitano il portale, scrivono via WhatsApp o rispondono all&apos;email.
              </p>
            </div>
          </div>
          <Link
            href={'/contatti'}
            className="shrink-0 text-xs font-semibold text-primary hover:underline"
          >
            Vedi contatti →
          </Link>
        </div>
      )}

      {/* AI suggestion banner — only on default mode ----------------- */}
      {!isHotMode && predictionsByLead.size > 0 && (
        <div className="flex items-center gap-3 rounded-xl bg-primary/10 px-5 py-3 text-sm">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-on-primary">
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor" aria-hidden>
              <path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z" />
            </svg>
          </div>
          <div className="flex-1">
            <p className="font-semibold text-on-surface">
              L&apos;agente AI suggerisce{' '}
              <span className="text-primary">{predictionsByLead.size}</span>{' '}
              lead da chiamare oggi
            </p>
            <p className="text-xs text-on-surface-variant">
              Sono in cima alla lista, evidenziati in verde. Clicca il badge
              <span className="mx-1 rounded-full bg-primary/20 px-1.5 py-0.5 text-[9px] font-bold uppercase text-primary">
                AI
              </span>
              accanto al nome per vedere perché.
            </p>
          </div>
        </div>
      )}

      {/* Territory filter banner — active when the user landed here
          via a "Territorio" chip click. Compact, dismissible. */}
      {sp.territorio && (
        <div className="flex items-center gap-3 rounded-xl bg-primary-container/40 px-4 py-2 text-sm">
          <span className="text-on-primary-container">
            🗺️ Filtro territorio attivo — mostro solo i lead originati da questa zona
          </span>
          <a
            href={queryFor({ territorio: undefined, page: undefined })}
            className="ml-auto rounded-full bg-surface px-3 py-1 text-xs font-semibold hover:opacity-80"
          >
            Rimuovi filtro
          </a>
        </div>
      )}

      {/* Filters ------------------------------------------------------ */}
      {!isHotMode && (
        <BentoCard padding="tight" span="full">
          <div className="flex flex-wrap gap-6 px-2 py-2">
            <FilterGroup label="Priorità">
              {TIERS.map((t) => (
                <FilterChip
                  key={t.value || 'all'}
                  active={(filter.tier ?? '') === t.value}
                  href={queryFor({ tier: t.value || undefined, page: undefined })}
                >
                  {t.label}
                </FilterChip>
              ))}
            </FilterGroup>
            <FilterGroup label="Stato">
              {STATUSES.map((s) => (
                <FilterChip
                  key={s.value || 'all'}
                  active={(filter.status ?? '') === s.value}
                  href={queryFor({ status: s.value || undefined, page: undefined })}
                >
                  {s.label}
                </FilterChip>
              ))}
            </FilterGroup>
            {/* Attività — segnali di engagement reali, accorpati in un
                solo gruppo: chip indipendenti che si accendono/spengono. */}
            <FilterGroup label="Attività">
              <FilterChip
                active={sp.read === '1'}
                href={queryFor({
                  read: sp.read === '1' ? undefined : '1',
                  page: undefined,
                })}
              >
                Email aperta
              </FilterChip>
              <FilterChip
                active={sp.portal === '1'}
                href={queryFor({
                  portal: sp.portal === '1' ? undefined : '1',
                  page: undefined,
                })}
              >
                Portale visitato
              </FilterChip>
            </FilterGroup>
            <FilterGroup label="Gestione">
              <FilterChip
                active={sp.gestione === undefined}
                href={queryFor({ gestione: undefined, page: undefined })}
              >
                Tutti
              </FilterChip>
              <FilterChip
                active={sp.gestione === 'auto'}
                href={queryFor({ gestione: 'auto', page: undefined })}
              >
                Auto
              </FilterChip>
              <FilterChip
                active={sp.gestione === 'manual'}
                href={queryFor({ gestione: 'manual', page: undefined })}
              >
                Manuale
              </FilterChip>
            </FilterGroup>
          </div>
        </BentoCard>
      )}

      {/* Table -------------------------------------------------------- */}
      <BentoCard padding="tight" span="full">
        {rows.length === 0 ? (
          <div className="rounded-lg bg-surface-container-low p-12 text-center">
            {isHotMode ? (
              <div className="space-y-2">
                <p className="text-sm text-on-surface-variant">
                  Nessuno è ancora atterrato sul portale nelle ultime 72h.
                </p>
                {contattiSummary.l4_qualified > 0 ? (
                  <p className="text-xs text-on-surface-variant">
                    Hai{' '}
                    <Link
                      href="/contatti"
                      className="font-semibold text-primary hover:underline"
                    >
                      {formatNumber(contattiSummary.l4_qualified)} contatti qualificati
                    </Link>{' '}
                    in attesa: l&apos;outreach automatico inizia a contattarli
                    appena la pipeline è attiva.
                  </p>
                ) : (
                  <p className="text-xs text-on-surface-variant">
                    Manda un round di outreach per riscaldare la pipeline.
                  </p>
                )}
              </div>
            ) : (
              <p className="text-sm text-on-surface-variant">
                Nessun lead trovato con questi filtri.
              </p>
            )}
          </div>
        ) : (
          <LeadsTable
            rows={rows}
            pipelineLabels={pipelineLabels}
            predictionsByLead={predictionsByLead}
          />
        )}

        {/* Pagination --------------------------------------------- */}
        {totalPages > 1 && (
          <div className="mt-4 flex items-center justify-between px-2">
            <span className="text-xs text-on-surface-variant">
              Pagina {page} di {totalPages}
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
// Filter UI (local components — consumed only here)
// ---------------------------------------------------------------------------

function FilterGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
        {label}
      </span>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

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
