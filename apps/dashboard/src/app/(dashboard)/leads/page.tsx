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

import Link, { type LinkProps } from 'next/link';
import { redirect } from 'next/navigation';

import { BentoCard } from '@/components/ui/bento-card';
import { GradientButton } from '@/components/ui/gradient-button';
import { EngagementScoreChip } from '@/components/ui/engagement-score-chip';
import { HotLeadsNow } from '@/components/hot-leads-now';
import { StatusChip, TierChip } from '@/components/ui/status-chip';
import {
  LEADS_PAGE_SIZE,
  listLeads,
  type LeadListFilter,
} from '@/lib/data/leads';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { cn, daysSince, relativeTime } from '@/lib/utils';
import type { LeadScoreTier, LeadStatus } from '@/types/db';

export const dynamic = 'force-dynamic';

type Search = Promise<{
  page?: string;
  tier?: string;
  status?: string;
  q?: string;
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
  const filter: LeadListFilter = {
    tier: (sp.tier as LeadScoreTier) || undefined,
    status: (sp.status as LeadStatus) || undefined,
    q: sp.q || undefined,
  };

  const [ctx, { rows, total }] = await Promise.all([
    getCurrentTenantContext(),
    listLeads({ page, filter }),
  ]);
  if (!ctx) redirect('/login');
  const totalPages = Math.max(1, Math.ceil(total / LEADS_PAGE_SIZE));

  const queryFor = (overrides: Record<string, string | undefined>) => {
    const params = new URLSearchParams();
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
            Pipeline · {total.toLocaleString('it-IT')} lead
          </p>
          <h1 className="font-headline text-4xl font-bold tracking-tighter">
            Leads
          </h1>
        </div>
        <GradientButton href="/territories" size="sm" variant="secondary">
          Aggiungi territorio
        </GradientButton>
      </header>

      {/* Real-time heat panel ---------------------------------------- */}
      <HotLeadsNow minutes={60} limit={5} />

      {/* Filters ------------------------------------------------------ */}
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

      {/* Table -------------------------------------------------------- */}
      <BentoCard padding="tight" span="full">
        {rows.length === 0 ? (
          <div className="rounded-lg bg-surface-container-low p-12 text-center">
            <p className="text-sm text-on-surface-variant">
              Nessun lead trovato con questi filtri.
            </p>
          </div>
        ) : (
          <div className="overflow-hidden rounded-lg bg-surface-container-low">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  <th className="px-5 py-3">Lead</th>
                  <th className="px-5 py-3">Tipo</th>
                  <th className="px-5 py-3">Comune</th>
                  <th className="px-5 py-3 text-right">kWp</th>
                  <th className="px-5 py-3 text-right">Score</th>
                  <th className="px-5 py-3">Tier</th>
                  <th className="px-5 py-3">Engagement</th>
                  <th className="px-5 py-3">Stato</th>
                  <th className="px-5 py-3">Ultimo tocco</th>
                  <th className="px-5 py-3" />
                </tr>
              </thead>
              <tbody className="bg-surface-container-lowest">
                {rows.map((lead, idx) => {
                  const name =
                    lead.subjects?.business_name ||
                    [
                      lead.subjects?.owner_first_name,
                      lead.subjects?.owner_last_name,
                    ]
                      .filter(Boolean)
                      .join(' ') ||
                    '—';
                  const lastTouch =
                    lead.dashboard_visited_at ||
                    lead.outreach_opened_at ||
                    lead.outreach_sent_at ||
                    lead.created_at;
                  const age = daysSince(lead.outreach_sent_at);
                  return (
                    <tr
                      key={lead.id}
                      className="transition-colors hover:bg-surface-container-low"
                      style={
                        idx !== 0
                          ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }
                          : undefined
                      }
                    >
                      <td className="px-5 py-4 font-semibold text-on-surface">
                        {name}
                      </td>
                      <td className="px-5 py-4 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                        {lead.subjects?.type ?? '—'}
                      </td>
                      <td className="px-5 py-4 text-on-surface-variant">
                        {lead.roofs?.comune ?? '—'}
                      </td>
                      <td className="px-5 py-4 text-right tabular-nums">
                        {lead.roofs?.estimated_kwp ?? '—'}
                      </td>
                      <td className="px-5 py-4 text-right font-headline font-bold tabular-nums">
                        {lead.score}
                      </td>
                      <td className="px-5 py-4">
                        <TierChip tier={lead.score_tier} />
                      </td>
                      <td className="px-5 py-4">
                        <EngagementScoreChip
                          score={lead.engagement_score}
                          updatedAt={lead.engagement_score_updated_at}
                        />
                      </td>
                      <td className="px-5 py-4">
                        <StatusChip status={lead.pipeline_status} />
                      </td>
                      <td className="px-5 py-4 text-xs text-on-surface-variant">
                        {relativeTime(lastTouch)}
                        {age !== null && lead.outreach_sent_at && (
                          <span className="ml-1 opacity-60">({age}gg)</span>
                        )}
                      </td>
                      <td className="px-5 py-4 text-right">
                        <Link
                          href={`/leads/${lead.id}`}
                          className="text-xs font-semibold text-primary hover:underline"
                        >
                          apri →
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
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
