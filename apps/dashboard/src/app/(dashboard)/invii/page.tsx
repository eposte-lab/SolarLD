/**
 * Invii — elenco flat di tutti gli invii outreach.
 *
 * Differenza rispetto a /campaigns:
 *   /campaigns  — vista management con KPI aggregati (delivery/open/click)
 *   /invii      — lista completa di ogni singolo send, con full engagement
 *                 inline per ogni riga (consegnato / aperto / cliccato)
 *
 * Ordinati per data invio DESC. Paginati 100 per pagina.
 * Filtri: canale, stato.
 */

import Link, { type LinkProps } from 'next/link';
import { redirect } from 'next/navigation';

import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { GradientButton } from '@/components/ui/gradient-button';
import { KpiChipCard } from '@/components/ui/kpi-chip-card';
import {
  getCampaignDeliveryStats,
  listCampaigns,
} from '@/lib/data/campaigns';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { cn, formatNumber, formatPercent, relativeTime } from '@/lib/utils';

export const dynamic = 'force-dynamic';

type Search = Promise<{
  page?: string;
  channel?: string;
  status?: string;
}>;

const CHANNEL_OPTIONS = [
  { value: '', label: 'Tutti i canali' },
  { value: 'email', label: 'Email' },
  { value: 'postal', label: 'Postale' },
  { value: 'whatsapp', label: 'WhatsApp' },
];

const STATUS_OPTIONS = [
  { value: '', label: 'Tutti gli stati' },
  { value: 'sent', label: 'Inviato' },
  { value: 'delivered', label: 'Consegnato' },
  { value: 'failed', label: 'Fallito' },
  { value: 'cancelled', label: 'Cancellato' },
];

const PAGE_SIZE = 100;

export default async function InviiPage({
  searchParams,
}: {
  searchParams: Search;
}) {
  const sp = await searchParams;
  const page = Math.max(1, Number(sp.page) || 1);
  const channelFilter = sp.channel || '';
  const statusFilter = sp.status || '';

  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const [stats, allCampaigns] = await Promise.all([
    getCampaignDeliveryStats(),
    listCampaigns(PAGE_SIZE * page), // simple over-fetch for now; works fine up to ~5k rows
  ]);

  // Apply client-side filters (avoids a new DB function for now)
  const filtered = allCampaigns.filter((c) => {
    if (channelFilter && c.channel !== channelFilter) return false;
    if (statusFilter && c.status !== statusFilter) return false;
    return true;
  });

  const paginated = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const total = filtered.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const queryFor = (overrides: Record<string, string | undefined>) => {
    const params = new URLSearchParams();
    if (channelFilter) params.set('channel', channelFilter);
    if (statusFilter) params.set('status', statusFilter);
    if (page > 1) params.set('page', String(page));
    for (const [k, v] of Object.entries(overrides)) {
      if (v === undefined || v === '') params.delete(k);
      else params.set(k, v);
    }
    const s = params.toString();
    return s ? `/invii?${s}` : '/invii';
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <header className="flex items-end justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Outreach · {formatNumber(stats.total)} invii totali
          </p>
          <h1 className="font-headline text-4xl font-bold tracking-tighter">
            Invii
          </h1>
        </div>
      </header>

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
          accent="secondary"
        />
      </BentoGrid>

      {/* Filters */}
      <BentoCard padding="tight" span="full">
        <div className="flex flex-wrap gap-6 px-2 py-2">
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
        </div>
      </BentoCard>

      {/* Table */}
      <BentoCard padding="tight" span="full">
        <header className="flex items-center justify-between px-2 pb-4 pt-2">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Storico completo
            </p>
            <h2 className="font-headline text-2xl font-bold tracking-tighter">
              {formatNumber(total)} invii
              {(channelFilter || statusFilter) && ' (filtrati)'}
            </h2>
          </div>
          {/* Link to A/B experiments */}
          <Link
            href="/experiments"
            className="text-xs font-semibold text-primary hover:underline"
          >
            A/B Testing →
          </Link>
        </header>

        {paginated.length === 0 ? (
          <div className="rounded-lg bg-surface-container-low p-10 text-center">
            <p className="text-sm text-on-surface-variant">
              Nessun invio trovato con questi filtri.
            </p>
          </div>
        ) : (
          <div className="overflow-hidden rounded-lg bg-surface-container-low">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  <th className="px-5 py-3">Data invio</th>
                  <th className="px-5 py-3">Step</th>
                  <th className="px-5 py-3">Canale</th>
                  <th className="px-5 py-3 max-w-xs">Subject / Template</th>
                  <th className="px-5 py-3">Stato invio</th>
                  <th className="px-5 py-3">Consegnato</th>
                  <th className="px-5 py-3">Aperto</th>
                  <th className="px-5 py-3">Click</th>
                  <th className="px-5 py-3 text-right">Costo</th>
                  <th className="px-5 py-3" />
                </tr>
              </thead>
              <tbody className="bg-surface-container-lowest">
                {paginated.map((c, idx) => (
                  <tr
                    key={c.id}
                    className="transition-colors hover:bg-surface-container-low"
                    style={
                      idx !== 0
                        ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }
                        : undefined
                    }
                  >
                    <td className="px-5 py-3 text-xs text-on-surface-variant">
                      {relativeTime(c.sent_at)}
                    </td>
                    <td className="px-5 py-3 text-center font-headline font-bold tabular-nums">
                      #{c.sequence_step}
                    </td>
                    <td className="px-5 py-3">
                      <ChannelChip channel={c.channel} />
                    </td>
                    <td className="max-w-xs truncate px-5 py-3 text-xs font-medium text-on-surface">
                      {c.email_subject ?? c.template_id ?? (
                        <span className="text-on-surface-variant">—</span>
                      )}
                    </td>
                    <td className="px-5 py-3">
                      <CampaignStatusChip status={c.status} />
                    </td>
                    {/* Engagement — read from lead */}
                    <td className="px-5 py-3 text-xs">
                      {c.leads?.outreach_delivered_at || c.status === 'delivered' ? (
                        <span className="font-semibold text-primary">✓</span>
                      ) : (
                        <span className="text-on-surface-variant">—</span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-xs">
                      {c.leads?.outreach_opened_at ? (
                        <span className="font-semibold text-primary">✓</span>
                      ) : (
                        <span className="text-on-surface-variant">—</span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-xs">
                      {c.leads?.outreach_clicked_at ? (
                        <span className="font-semibold text-primary">✓</span>
                      ) : (
                        <span className="text-on-surface-variant">—</span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-right tabular-nums text-xs text-on-surface-variant">
                      {c.cost_cents > 0
                        ? `€ ${(c.cost_cents / 100).toFixed(2)}`
                        : '—'}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <Link
                        href={`/leads/${c.lead_id}`}
                        className="text-xs font-semibold text-primary hover:underline"
                      >
                        lead →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
      href={href as LinkProps<string>['href']}
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
