/**
 * Invii — elenco flat di tutti gli invii outreach (tabella outreach_sends).
 *
 * Differenza rispetto a /campaigns:
 *   /campaigns  — gestione campagne di acquisizione (entità strategiche)
 *   /invii      — lista completa di ogni singolo send, con full engagement
 *                 inline per ogni riga (consegnato / aperto / cliccato)
 *
 * Ordinati per data invio DESC. Paginati 100 per pagina.
 * Filtri: canale, stato.
 *
 * Reads from `outreach_sends` (ex `campaigns`, renamed in migration 0043).
 */

import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Check,
} from 'lucide-react';
import Link from 'next/link';
import { redirect } from 'next/navigation';

import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { CollapsibleFilters } from '@/components/ui/collapsible-filters';
import { GradientButton } from '@/components/ui/gradient-button';
import { KpiChipCard } from '@/components/ui/kpi-chip-card';
import { InviiTable } from '@/components/invii/invii-table';
import { SearchBox } from '@/components/ui/search-box';
import { isPremiumSource } from '@/components/premium-email-field';
import { ExportCsvButton } from '@/components/invii/export-csv-button';
import {
  getCampaignDeliveryStats,
  listCampaigns,
} from '@/lib/data/campaigns';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { getQualificationReport } from '@/lib/data/qualification-report';
import { createSupabaseServerClient } from '@/lib/supabase/server';
import { QualificationReportPanel } from './QualificationReportPanel';
import { AutoRefresh } from './AutoRefresh';
import { cn, formatNumber, formatPercent, relativeTime } from '@/lib/utils';

export const dynamic = 'force-dynamic';

type Search = Promise<{
  page?: string;
  channel?: string;
  status?: string;
  tab?: string;
  premium?: string;
  q?: string;
  range?: string;
}>;

// Analysis window presets for the "Periodo" selector. '' = all-time (default).
const RANGE_OPTIONS: { value: string; label: string; days: number | null }[] = [
  { value: '7', label: '7 giorni', days: 7 },
  { value: '30', label: '30 giorni', days: 30 },
  { value: '90', label: '90 giorni', days: 90 },
  { value: '', label: 'Tutto', days: null },
];

const CHANNEL_OPTIONS = [
  { value: '', label: 'Tutti i canali' },
  { value: 'email', label: 'Email' },
  { value: 'postal', label: 'Postale' },
];

const STATUS_OPTIONS = [
  { value: '', label: 'Tutti gli stati' },
  { value: 'sent', label: 'Inviato' },
  { value: 'delivered', label: 'Consegnato' },
  { value: 'failed', label: 'Fallito' },
  { value: 'cancelled', label: 'Cancellato' },
];

const PAGE_SIZE = 100;

/** A deferred lead — triggered daily_target_cap_reached event today. */
interface DeferredItem {
  lead_id: string | null;
  occurred_at: string;
  payload: Record<string, unknown>;
}

async function getDeferredToday(): Promise<DeferredItem[]> {
  const sb = await createSupabaseServerClient();
  const midnightUtc = new Date();
  midnightUtc.setUTCHours(0, 0, 0, 0);
  const { data } = await sb
    .from('events')
    .select('lead_id, occurred_at, payload')
    .eq('event_type', 'lead.outreach_ratelimited')
    .gte('occurred_at', midnightUtc.toISOString())
    .order('occurred_at', { ascending: false })
    .limit(500);
  const events = (data ?? []) as DeferredItem[];

  // A deferred event is historical: a lead rate-limited earlier today may have
  // since been blacklisted (e.g. existing-PV detected) or already sent. Those
  // must NOT keep showing as "scheduled for tomorrow" — the daily send won't
  // pick them (warehouse_pick only takes ready_to_send; outreach hard-stops on
  // blacklisted). Drop only the leads we POSITIVELY know are out, so an
  // RLS-hidden row is never silently removed.
  const leadIds = [...new Set(events.map((e) => e.lead_id).filter((id): id is string => !!id))];
  if (leadIds.length === 0) return events;
  const { data: leadRows } = await sb
    .from('leads')
    .select('id, pipeline_status, outreach_sent_at')
    .in('id', leadIds);
  const excluded = new Set(
    (leadRows ?? [])
      .filter(
        (l) =>
          l.outreach_sent_at != null ||
          ['blacklisted', 'closed_lost', 'closed_won'].includes(String(l.pipeline_status)),
      )
      .map((l) => l.id as string),
  );
  return events.filter((e) => !e.lead_id || !excluded.has(e.lead_id));
}

export default async function InviiPage({
  searchParams,
}: {
  searchParams: Search;
}) {
  const sp = await searchParams;
  const page = Math.max(1, Number(sp.page) || 1);
  const channelFilter = sp.channel || '';
  const statusFilter = sp.status || '';
  const premiumFilter = sp.premium === '1';
  const activeTab = sp.tab === 'rimandati' ? 'rimandati' : 'storico';
  const searchRaw = (sp.q ?? '').trim();
  const search = searchRaw.toLowerCase();
  const activeRange =
    RANGE_OPTIONS.find((r) => r.value === (sp.range ?? '')) ?? RANGE_OPTIONS[3]!;
  const rangeDays = activeRange.days;

  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const [stats, allCampaigns, deferred, qualReport] = await Promise.all([
    // KPI strip — scoped to the selected analysis window (default: all-time).
    getCampaignDeliveryStats({ sinceDays: rangeDays }),
    // Over-fetch for client-side filtering. When searching, widen the window
    // so the query covers the whole send history, not just the current page.
    listCampaigns(search ? 5000 : PAGE_SIZE * page, { sinceDays: rangeDays }),
    getDeferredToday(),
    // Qualified-vs-legacy send comparison (all-time, RLS-scoped via RPC).
    getQualificationReport(),
  ]);

  // Apply client-side filters (avoids a new DB function for now)
  const filtered = allCampaigns.filter((c) => {
    if (channelFilter && c.channel !== channelFilter) return false;
    if (statusFilter && c.status !== statusFilter) return false;
    if (premiumFilter && !isPremiumSource(c.leads?.subjects?.decision_maker_email_source))
      return false;
    if (search) {
      const s = c.leads?.subjects;
      const hay = [s?.business_name, s?.decision_maker_name, s?.decision_maker_email]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      if (!hay.includes(search)) return false;
    }
    return true;
  });

  const paginated = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const total = filtered.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const queryFor = (overrides: Record<string, string | undefined>) => {
    const params = new URLSearchParams();
    if (channelFilter) params.set('channel', channelFilter);
    if (statusFilter) params.set('status', statusFilter);
    if (premiumFilter) params.set('premium', '1');
    if (searchRaw) params.set('q', searchRaw);
    if (activeRange.value) params.set('range', activeRange.value);
    if (page > 1) params.set('page', String(page));
    for (const [k, v] of Object.entries(overrides)) {
      if (v === undefined || v === '') params.delete(k);
      else params.set(k, v);
    }
    const s = params.toString();
    return s ? `/invii?${s}` : '/invii';
  };

  const activeFilterCount = [channelFilter, statusFilter, premiumFilter ? '1' : ''].filter(
    Boolean,
  ).length;

  return (
    <div className="space-y-4">
      {/* Mantiene i numeri di questa pagina aggiornati in tempo reale (soft refresh). */}
      <AutoRefresh seconds={60} />
      {/* Header */}
      <header className="flex items-end justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Outreach · {formatNumber(stats.total)} invii
            {rangeDays ? ` · ultimi ${rangeDays} giorni` : ' totali'}
          </p>
          <h1 className="font-headline text-2xl font-bold tracking-tighter md:text-4xl">
            Invii
          </h1>
        </div>
        <div className="flex items-center gap-3">
          <SearchBox placeholder="Cerca azienda, referente…" />
          <ExportCsvButton />
        </div>
      </header>

      {/* Tab bar */}
      <div className="flex gap-1 rounded-xl bg-surface-container-low p-1">
        <Link
          href="/invii"
          className={cn(
            'flex-1 rounded-lg px-3 py-2 text-center text-sm font-semibold transition-colors',
            activeTab === 'storico'
              ? 'bg-surface text-on-surface shadow-ambient-sm'
              : 'text-on-surface-variant hover:bg-surface-container-high',
          )}
        >
          Storico invii
        </Link>
        <Link
          href="/invii?tab=rimandati"
          className={cn(
            'flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold transition-colors',
            activeTab === 'rimandati'
              ? 'bg-surface text-on-surface shadow-ambient-sm'
              : 'text-on-surface-variant hover:bg-surface-container-high',
          )}
        >
          Rimandati a domani
          {deferred.length > 0 && (
            <span className="rounded-full bg-tertiary px-2 py-0.5 text-[10px] font-bold text-on-tertiary">
              {deferred.length}
            </span>
          )}
        </Link>
      </div>

      {/* ── Tab: Rimandati a domani ──────────────────────────────────────── */}
      {activeTab === 'rimandati' && (
        <BentoCard span="full" padding="tight">
          <header className="px-4 pb-3 pt-4">
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Cap giornaliero raggiunto · {deferred.length} lead bloccati oggi
            </p>
            <h2 className="font-headline text-2xl font-bold tracking-tighter">
              Rimandati a domani
            </h2>
            <p className="mt-1 text-xs text-on-surface-variant">
              Questi lead avrebbero ricevuto un invio oggi ma il cap contrattuale (250/giorno)
              era già esaurito. Il follow-up scheduler li riprova automaticamente domani.
            </p>
          </header>
          {deferred.length === 0 ? (
            <div className="px-4 pb-8 pt-4 text-center">
              <p className="text-sm text-on-surface-variant">
                Nessun invio rimandato oggi. Il cap non è stato raggiunto.
              </p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-outline-variant/30 text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  <th className="px-4 py-3">Lead</th>
                  <th className="px-4 py-3">Cap al momento</th>
                  <th className="px-4 py-3">Orario</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-outline-variant/20">
                {deferred.map((item, i) => {
                  const p = item.payload as { cap?: number; used?: number };
                  return (
                    <tr key={`${item.lead_id ?? 'unknown'}-${i}`} className="hover:bg-surface-container-low">
                      <td className="px-4 py-3">
                        {item.lead_id ? (
                          <Link
                            href={`/leads/${item.lead_id}`}
                            className="font-mono text-xs text-primary hover:underline"
                          >
                            {item.lead_id.slice(0, 8)}…
                          </Link>
                        ) : (
                          <span className="text-xs text-on-surface-variant">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-on-surface-variant">
                        {p.used ?? '?'} / {p.cap ?? 250}
                      </td>
                      <td className="px-4 py-3 text-xs text-on-surface-variant">
                        {relativeTime(item.occurred_at)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </BentoCard>
      )}

      {/* ── Tab: Storico invii ───────────────────────────────────────────── */}
      {activeTab === 'storico' && <>

      {/* Qualified-vs-legacy send comparison — shows the lift the contact
          qualification (NeverBounce + premium) delivers. All-time. */}
      <QualificationReportPanel report={qualReport} />

      {/* Periodo di analisi — scopes the KPI strip + the table below to a
          rolling window so the operator can read "in N giorni quanti invii,
          che open rate". Default = Tutto (all-time). */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Periodo
        </span>
        <div className="flex flex-wrap gap-1.5">
          {RANGE_OPTIONS.map((opt) => (
            <FilterChip
              key={opt.value || 'all-range'}
              active={activeRange.value === opt.value}
              href={queryFor({ range: opt.value || undefined, page: undefined })}
            >
              {opt.label}
            </FilterChip>
          ))}
        </div>
      </div>

      {/* KPI strip */}
      <BentoGrid cols={5}>
        <div className="md:col-span-1">
          <KpiChipCard
            label="Totali"
            value={formatNumber(stats.total)}
            accent="neutral"
          />
        </div>
        <KpiChipCard
          label="Consegna"
          value={formatPercent(stats.delivery_rate, 1)}
          hint={`${stats.delivered} / ${stats.total}`}
          accent="primary"
        />
        <KpiChipCard
          label="Open rate"
          value={formatPercent(stats.open_rate, 1)}
          hint={`${stats.opened} aperti`}
          accent="tertiary"
        />
        <KpiChipCard
          label="Click rate"
          value={formatPercent(stats.click_rate, 1)}
          hint={`${stats.clicked} click`}
          accent="primary"
        />
        <KpiChipCard
          label="Falliti"
          value={formatNumber(stats.failed)}
          hint="Bounce / errore"
          tone={stats.failed > 0 ? 'critical' : 'neutral'}
        />
      </BentoGrid>

      {/* Filters */}
      <CollapsibleFilters
        activeCount={activeFilterCount}
        resetHref={queryFor({
          channel: undefined,
          status: undefined,
          premium: undefined,
          page: undefined,
        })}
      >
        <FilterGroup label="Canale">
          {CHANNEL_OPTIONS.map((opt) => (
            <FilterChip
              key={opt.value || 'all-ch'}
              active={channelFilter === opt.value}
              href={queryFor({ channel: opt.value || undefined, page: undefined })}
            >
              {opt.label}
            </FilterChip>
          ))}
        </FilterGroup>
        <FilterGroup label="Stato">
          {STATUS_OPTIONS.map((opt) => (
            <FilterChip
              key={opt.value || 'all-st'}
              active={statusFilter === opt.value}
              href={queryFor({ status: opt.value || undefined, page: undefined })}
            >
              {opt.label}
            </FilterChip>
          ))}
        </FilterGroup>
        <FilterGroup label="Contatto">
          <FilterChip
            active={!premiumFilter}
            href={queryFor({ premium: undefined, page: undefined })}
          >
            Tutti
          </FilterChip>
          <FilterChip
            active={premiumFilter}
            href={queryFor({ premium: '1', page: undefined })}
          >
            Solo verificati
          </FilterChip>
        </FilterGroup>
      </CollapsibleFilters>

      {/* Table */}
      <BentoCard padding="tight" span="full">
        <header className="flex items-center justify-between px-2 pb-4 pt-2">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Storico completo
            </p>
            <h2 className="font-headline text-2xl font-bold tracking-tighter">
              {formatNumber(total)} invii
              {(channelFilter || statusFilter || premiumFilter) && ' (filtrati)'}
            </h2>
          </div>
          {/* Link to A/B experiments */}
          <Link
            href="/experiments"
            className="group/link inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline"
          >
            A/B Testing
            <ArrowUpRight
              size={12}
              strokeWidth={2.5}
              className="transition-transform group-hover/link:translate-x-0.5 group-hover/link:-translate-y-0.5"
              aria-hidden
            />
          </Link>
        </header>

        {paginated.length === 0 ? (
          <div className="rounded-lg bg-surface-container-low p-10 text-center">
            <p className="text-sm text-on-surface-variant">
              Nessun invio trovato con questi filtri.
            </p>
          </div>
        ) : (
          <InviiTable rows={paginated} />
        )}
        {/* Pagination */}
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
                  <ArrowLeft size={12} strokeWidth={2.25} aria-hidden />
                  Precedente
                </GradientButton>
              )}
              {page < totalPages && (
                <GradientButton
                  href={queryFor({ page: String(page + 1) })}
                  variant="secondary"
                  size="sm"
                >
                  Successiva
                  <ArrowRight size={12} strokeWidth={2.25} aria-hidden />
                </GradientButton>
              )}
            </div>
          </div>
        )}
      </BentoCard>
      </>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Local helpers
// ---------------------------------------------------------------------------

function ChannelChip({ channel }: { channel: string }) {
  const styles: Record<string, string> = {
    email: 'bg-primary-container/60 text-on-primary-container',
    postal: 'bg-tertiary-container/60 text-on-tertiary-container',
    whatsapp: 'bg-surface-container-highest text-on-surface',
  };
  const labels: Record<string, string> = {
    email: 'Email',
    postal: 'Postale',
    whatsapp: 'WhatsApp',
  };
  return (
    <span
      className={cn(
        'inline-flex rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest',
        styles[channel] ?? 'bg-surface-container text-on-surface-variant',
      )}
    >
      {labels[channel] ?? channel}
    </span>
  );
}

function CampaignStatusChip({ status }: { status: string }) {
  const styles: Record<string, string> = {
    pending: 'bg-surface-container-high text-on-surface-variant',
    sent: 'bg-surface-container-highest text-on-surface',
    delivered: 'bg-primary-container text-on-primary-container',
    failed: 'bg-secondary-container text-on-secondary-container',
    cancelled: 'bg-surface-container text-on-surface-variant opacity-70',
  };
  const labels: Record<string, string> = {
    pending: 'In coda',
    sent: 'Inviato',
    delivered: 'Consegnato',
    failed: 'Fallito',
    cancelled: 'Cancellato',
  };
  return (
    <span
      className={cn(
        'inline-flex rounded-md px-2 py-0.5 text-xs font-medium',
        styles[status] ?? 'bg-surface-container text-on-surface-variant',
      )}
    >
      {labels[status] ?? status}
    </span>
  );
}

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
