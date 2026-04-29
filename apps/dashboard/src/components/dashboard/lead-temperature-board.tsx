'use client';

/**
 * LeadTemperatureBoard — client-side sortable table of top leads.
 *
 * Columns:
 *   Temperatura (score_tier chip), Ragione sociale, Zona (comune),
 *   Score, Ultimo evento, P.Conv. (conversion probability),
 *   Valore est. (kWp × €1500), Azione
 *
 * Sort: click any column header to toggle ASC/DESC.
 * Initial sort: score DESC.
 *
 * This is a client component because sorting is interactive.
 * Data is passed as a prop from the server page component.
 */

import { ArrowUpRight } from 'lucide-react';
import Link from 'next/link';

import type { LeadListRow } from '@/types/db';
import { useSortableData } from '@/hooks/use-sortable-data';
import { SortableTh } from '@/components/ui/sortable-th';
import { cn, relativeTime } from '@/lib/utils';

// ── types ─────────────────────────────────────────────────────────────────────

type SortKey = 'score' | 'tier' | 'name' | 'zone' | 'last_event' | 'p_conv' | 'value_eur';

// ── helpers ───────────────────────────────────────────────────────────────────

const TIER_ORDER: Record<string, number> = { hot: 3, warm: 2, cold: 1, rejected: 0 };

/** p(conversion) — rough heuristic based on pipeline_status. */
function conversionProb(status: string): number {
  const MAP: Record<string, number> = {
    closed_won: 1.0,
    appointment: 0.55,
    whatsapp: 0.35,
    clicked: 0.2,
    engaged: 0.2,
    opened: 0.15,
    delivered: 0.08,
    sent: 0.05,
    new: 0.03,
    closed_lost: 0,
    blacklisted: 0,
    rejected: 0,
  };
  return MAP[status] ?? 0.05;
}

/** Estimated deal value in EUR from roi_data */
function estimatedEur(lead: LeadListRow): number {
  // roofs.estimated_kwp * €1500/kWp * conversion prob
  const kwp = (lead.roofs?.estimated_kwp ?? 8);
  return Math.round(kwp * 1500 * conversionProb(lead.pipeline_status));
}

function displayName(lead: LeadListRow): string {
  return (
    lead.subjects?.business_name ||
    [lead.subjects?.owner_first_name, lead.subjects?.owner_last_name]
      .filter(Boolean)
      .join(' ') ||
    '—'
  );
}

function lastEvent(lead: LeadListRow): string | null {
  return (
    lead.dashboard_visited_at ||
    lead.outreach_opened_at ||
    lead.outreach_sent_at ||
    null
  );
}

// ── Temperature chip ──────────────────────────────────────────────────────────

const TIER_CHIP: Record<string, { label: string; style: string; dot: string }> = {
  hot: {
    label: 'Hot',
    style: 'bg-warning/15 text-warning',
    dot: 'bg-warning animate-pulse',
  },
  warm: {
    label: 'Warm',
    style: 'bg-primary/15 text-primary',
    dot: 'bg-primary',
  },
  cold: {
    label: 'Freddo',
    style: 'bg-white/[0.04] text-on-surface-variant',
    dot: 'bg-on-surface-variant/40',
  },
  rejected: {
    label: 'Scartato',
    style: 'bg-white/[0.02] text-on-surface-variant/50',
    dot: 'bg-outline-variant/30',
  },
};

const DEFAULT_TIER_CFG = {
  label: 'Freddo',
  style: 'bg-white/[0.04] text-on-surface-variant',
  dot: 'bg-on-surface-variant/40',
} as const;

function TemperatureChip({ tier }: { tier: string }) {
  const cfg = TIER_CHIP[tier] ?? DEFAULT_TIER_CFG;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-semibold',
        cfg.style,
      )}
    >
      <span className={cn('h-1.5 w-1.5 rounded-full', cfg.dot)} />
      {cfg.label}
    </span>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

interface LeadTemperatureBoardProps {
  leads: LeadListRow[];
  className?: string;
}

export function LeadTemperatureBoard({ leads, className }: LeadTemperatureBoardProps) {
  const { sorted, sortKey, sortDir, requestSort } = useSortableData<LeadListRow, SortKey>(
    leads,
    (lead, key) => {
      switch (key) {
        case 'score':
          return lead.score;
        case 'tier':
          return TIER_ORDER[lead.score_tier] ?? 0;
        case 'name':
          return displayName(lead);
        case 'zone':
          return lead.roofs?.comune ?? '';
        case 'last_event':
          return lastEvent(lead);
        case 'p_conv':
          return conversionProb(lead.pipeline_status);
        case 'value_eur':
          return estimatedEur(lead);
      }
    },
    { initialKey: 'score', initialDir: 'desc' },
  );

  if (leads.length === 0) {
    return (
      <div className={className}>
        <div className="flex items-center justify-center rounded-xl bg-surface-container-low py-10">
          <p className="text-sm text-on-surface-variant">Nessun lead disponibile.</p>
        </div>
      </div>
    );
  }

  return (
    <div className={className}>
      <div className="overflow-hidden rounded-2xl liquid-glass-sm relative">
        <span
          className="pointer-events-none absolute inset-x-0 top-0 h-12 bg-glass-specular"
          aria-hidden
        />
        <table className="w-full text-sm relative">
          <thead>
            <tr>
              <SortableTh sortKey="tier" active={sortKey} dir={sortDir} onSort={requestSort}>Temp.</SortableTh>
              <SortableTh sortKey="name" active={sortKey} dir={sortDir} onSort={requestSort}>Azienda / Contatto</SortableTh>
              <SortableTh sortKey="zone" active={sortKey} dir={sortDir} onSort={requestSort}>Comune</SortableTh>
              <SortableTh sortKey="score" active={sortKey} dir={sortDir} onSort={requestSort}>Score</SortableTh>
              <SortableTh sortKey="last_event" active={sortKey} dir={sortDir} onSort={requestSort}>Ultimo evento</SortableTh>
              <SortableTh sortKey="p_conv" active={sortKey} dir={sortDir} onSort={requestSort}>P.Conv.</SortableTh>
              <SortableTh sortKey="value_eur" active={sortKey} dir={sortDir} onSort={requestSort}>Valore est.</SortableTh>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            {sorted.map((lead, idx) => {
              const name = displayName(lead);
              const last = lastEvent(lead);
              const pConv = conversionProb(lead.pipeline_status);
              const valEur = estimatedEur(lead);

              return (
                <tr
                  key={lead.id}
                  className="transition-colors hover:bg-white/[0.03]"
                  style={
                    idx !== 0
                      ? { boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.05)' }
                      : undefined
                  }
                >
                  <td className="px-4 py-3">
                    <TemperatureChip tier={lead.score_tier} />
                  </td>
                  <td className="max-w-[160px] truncate px-4 py-3 font-semibold text-on-surface">
                    {name}
                  </td>
                  <td className="px-4 py-3 text-xs text-on-surface-variant">
                    {lead.roofs?.comune ?? '—'}
                    {lead.roofs?.provincia ? ` (${lead.roofs.provincia})` : ''}
                  </td>
                  <td className="px-4 py-3 font-headline font-bold tabular-nums text-on-surface">
                    {lead.score}
                  </td>
                  <td className="px-4 py-3 text-xs text-on-surface-variant">
                    {last ? relativeTime(last) : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1.5">
                      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-container-high">
                        <div
                          className="h-full rounded-full transition-all"
                          style={{
                            width: `${pConv * 100}%`,
                            backgroundColor:
                              pConv >= 0.5
                                ? '#6FCF97'
                                : pConv >= 0.2
                                  ? '#5BB880'
                                  : '#8A9499',
                          }}
                        />
                      </div>
                      <span className="tabular-nums text-[10px] text-on-surface-variant">
                        {Math.round(pConv * 100)}%
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3 font-headline font-bold tabular-nums text-on-surface">
                    {valEur > 0 ? `€${valEur.toLocaleString('it-IT')}` : '—'}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      href={`/leads/${lead.id}`}
                      className="group/link inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline"
                    >
                      Apri
                      <ArrowUpRight
                        size={12}
                        strokeWidth={2.5}
                        className="transition-transform group-hover/link:translate-x-0.5 group-hover/link:-translate-y-0.5"
                        aria-hidden
                      />
                    </Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
