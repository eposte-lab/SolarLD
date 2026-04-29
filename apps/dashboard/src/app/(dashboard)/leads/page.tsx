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
import { HotLeadsNow } from '@/components/hot-leads-now';
import { LeadsTable } from '@/components/leads/leads-table';
import {
  LEADS_PAGE_SIZE,
  listHotLeadsAwaitingResponse,
  listLeads,
  type LeadListFilter,
} from '@/lib/data/leads';
import { getContattiSummary } from '@/lib/data/contatti';
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
}>;

const TIERS: { value: '' | LeadScoreTier; label: string }[] = [
  { value: '', label: 'Tutti' },
  { value: 'hot', label: 'Hot' },
  { value: 'warm', label: 'Warm' },
  { value: 'cold', label: 'Cold' },
];

const STATUSES: { value: '' | LeadStatus; label: string }[] = [
  { value: '', label: 'Tutti' },
  { value: 'new', label: 'Nuovo' },
  { value: 'sent', label: 'Inviato' },
  { value: 'delivered', label: 'Consegnato' },
  { value: 'opened', label: 'Aperto' },
  { value: 'clicked', label: 'Click' },
  { value: 'engaged', label: 'Engaged' },
  { value: 'whatsapp', label: 'WhatsApp' },
  { value: 'appointment', label: 'Appuntamento' },
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
  };

  // In "Caldi adesso" mode we ignore the tier/status filters because
  // they would conflict with the operational definition (engagement
  // ≥ 60, recent portal event, NOT in a closing pipeline stage).
  const [ctx, listResult, hotRows, contattiSummary] = await Promise.all([
    getCurrentTenantContext(),
    isHotMode
      ? Promise.resolve({ rows: [], total: 0 })
      : listLeads({ page, filter }),
    isHotMode
      ? listHotLeadsAwaitingResponse({ sinceHours: 72, minScore: 60, limit: 50 })
      : Promise.resolve([]),
    getContattiSummary(),
  ]);
  if (!ctx) redirect('/login');
  const { rows: regularRows, total } = listResult;
  const rows = isHotMode ? hotRows : regularRows;
  const effectiveTotal = isHotMode ? hotRows.length : total;
  const totalPages = isHotMode
    ? 1
    : Math.max(1, Math.ceil(total / LEADS_PAGE_SIZE));

  // Contatti in attesa = quelli arrivati a L4 ma non ancora in pipeline
  // (l4_qualified > total leads → differenza = ancora da promuovere)
  const contattiInAttesa = Math.max(
    0,
    contattiSummary.l4_qualified - total,
  );

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
              : `Pipeline attiva · ${effectiveTotal.toLocaleString('it-IT')} lead`}
          </p>
          <h1 className="font-headline text-4xl font-bold tracking-tighter">
            {isHotMode ? '🔥 Caldi adesso' : 'Lead Attivi'}
          </h1>
        </div>
        <GradientButton href="/territories" size="sm" variant="secondary">
          Aggiungi territorio
        </GradientButton>
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
          🔥 Caldi adesso
        </Link>
      </div>

      {isHotMode && (
        <div className="rounded-xl bg-surface-container-low px-5 py-3 text-xs text-on-surface-variant">
          Lead con engagement ≥ 60, evento sul portale nelle ultime 72h, non
          ancora in pipeline (engaged / WhatsApp / appuntamento / chiusi).
          Ordinati per score e ultimo evento.
        </div>
      )}

      {/* Contatti in attesa banner ------------------------------------ */}
      {contattiSummary.l1 > 0 && (
        <div className="flex items-center justify-between rounded-xl bg-surface-container-low px-5 py-3">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-tertiary-container text-on-tertiary-container">
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor">
                <path d="M15 12a3 3 0 100-6 3 3 0 000 6zm-9-1a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM15 14c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4zm-9 1c-.29 0-.62.02-.97.05C6.19 15.93 7 16.8 7 18v2H0v-2c0-2.21 2.69-3.4 6-3.95z" />
              </svg>
            </div>
            <div>
              <p className="text-sm font-semibold text-on-surface">
                {formatNumber(contattiSummary.l1)} contatti scansionati
                {contattiSummary.l4_qualified > 0 && (
                  <> · {formatNumber(contattiSummary.l4_qualified)} qualificati Solar</>
                )}
              </p>
              <p className="text-xs text-on-surface-variant">
                I contatti scansionati non sono ancora lead — diventano lead dopo scoring e outreach.
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

      {/* Real-time heat panel (only on default mode) ----------------- */}
      {!isHotMode && <HotLeadsNow minutes={60} limit={5} />}

      {/* Filters ------------------------------------------------------ */}
      {!isHotMode && (
        <BentoCard padding="tight" span="full">
          <div className="flex flex-wrap gap-6 px-2 py-2">
            <FilterGroup label="Tier">
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
          </div>
        </BentoCard>
      )}

      {/* Table -------------------------------------------------------- */}
      <BentoCard padding="tight" span="full">
        {rows.length === 0 ? (
          <div className="rounded-lg bg-surface-container-low p-12 text-center">
            <p className="text-sm text-on-surface-variant">
              {isHotMode
                ? 'Nessun lead caldo da chiamare. Manda un round di outreach per riscaldare la pipeline.'
                : 'Nessun lead trovato con questi filtri.'}
            </p>
          </div>
        ) : (
          <LeadsTable rows={rows} pipelineLabels={pipelineLabels} />
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
