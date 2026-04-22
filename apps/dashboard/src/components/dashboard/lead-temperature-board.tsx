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

import Link from 'next/link';
import { useState, useMemo, useCallback } from 'react';

import type { LeadListRow } from '@/types/db';
import { cn, relativeTime } from '@/lib/utils';

// ── types ─────────────────────────────────────────────────────────────────────

type SortKey = 'score' | 'tier' | 'name' | 'zone' | 'last_event' | 'p_conv' | 'value_eur';
type SortDir = 'asc' | 'desc';

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
    style: 'bg-[#1a73e8]/15 text-[#1a73e8]',
    dot: 'bg-[#1a73e8] animate-pulse',
  },
  warm: {
    label: 'Warm',
    style: 'bg-[#fdbb31]/20 text-[#a07000]',
    dot: 'bg-[#fdbb31]',
  },
  cold: {
    label: 'Freddo',
    style: 'bg-surface-container-high text-on-surface-variant',
    dot: 'bg-on-surface-variant/40',
  },
  rejected: {
    label: 'Scartato',
    style: 'bg-surface-container text-on-surface-variant/50',
    dot: 'bg-outline-variant/30',
  },
};

const DEFAULT_TIER_CFG = {
  label: 'Freddo',
  style: 'bg-surface-container-high text-on-surface-variant',
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

// ── Sort indicator ────────────────────────────────────────────────────────────

function SortIcon({ active, dir }: { active: boolean; dir: SortDir }) {
  if (!active) return <span className="text-on-surface-variant/30">↕</span>;
  return <span className="text-primary">{dir === 'asc' ? '↑' : '↓'}</span>;
}

// ── Main component ────────────────────────────────────────────────────────────

interface LeadTemperatureBoardProps {
  leads: LeadListRow[];
  className?: string;
}

export function LeadTemperatureBoard({ leads, className }: LeadTemperatureBoardProps) {
  const [sortKey, setSortKey] = useState<SortKey>('score');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const handleSort = useCallback(
    (key: SortKey) => {
      if (key === sortKey) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
      } else {
        setSortKey(key);
        setSortDir('desc');
      }
    },
    [sortKey],
  );

  const sorted = useMemo(() => {
    return [...leads].sort((a, b) => {
      let aVal: number | string = 0;
      let bVal: number | string = 0;

      switch (sortKey) {
        case 'score':
          aVal = a.score;
          bVal = b.score;
          break;
        case 'tier':
          aVal = TIER_ORDER[a.score_tier] ?? 0;
          bVal = TIER_ORDER[b.score_tier] ?? 0;
          break;
        case 'name':
          aVal = displayName(a).toLowerCase();
          bVal = displayName(b).toLowerCase();
          break;
        case 'zone':
          aVal = (a.roofs?.comune ?? '').toLowerCase();
          bVal = (b.roofs?.comune ?? '').toLowerCase();
          break;
        case 'last_event':
          aVal = lastEvent(a) ?? '';
          bVal = lastEvent(b) ?? '';
          break;
        case 'p_conv':
          aVal = conversionProb(a.pipeline_status);
          bVal = conversionProb(b.pipeline_status);
          break;
        case 'value_eur':
          aVal = estimatedEur(a);
          bVal = estimatedEur(b);
          break;
      }

      if (typeof aVal === 'string') {
        const cmp = aVal.localeCompare(bVal as string, 'it');
        return sortDir === 'asc' ? cmp : -cmp;
      }
      return sortDir === 'asc'
        ? (aVal as number) - (bVal as number)
        : (bVal as number) - (aVal as number);
    });
  }, [leads, sortKey, sortDir]);

  const thCls = (key: SortKey) =>
    cn(
      'cursor-pointer select-none whitespace-nowrap px-4 py-3 text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant',
      'hover:text-on-surface transition-colors',
      sortKey === key && 'text-on-surface',
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
      <div className="overflow-hidden rounded-xl bg-surface-container-low">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className={thCls('tier')} onClick={() => handleSort('tier')}>
                Temp. <SortIcon active={sortKey === 'tier'} dir={sortDir} />
              </th>
              <th className={thCls('name')} onClick={() => handleSort('name')}>
                Azienda / Contatto <SortIcon active={sortKey === 'name'} dir={sortDir} />
              </th>
              <th className={thCls('zone')} onClick={() => handleSort('zone')}>
                Comune <SortIcon active={sortKey === 'zone'} dir={sortDir} />
              </th>
              <th className={thCls('score')} onClick={() => handleSort('score')}>
                Score <SortIcon active={sortKey === 'score'} dir={sortDir} />
              </th>
              <th className={thCls('last_event')} onClick={() => handleSort('last_event')}>
                Ultimo evento <SortIcon active={sortKey === 'last_event'} dir={sortDir} />
              </th>
              <th className={thCls('p_conv')} onClick={() => handleSort('p_conv')}>
                P.Conv. <SortIcon active={sortKey === 'p_conv'} dir={sortDir} />
              </th>
              <th className={thCls('value_eur')} onClick={() => handleSort('value_eur')}>
                Valore est. <SortIcon active={sortKey === 'value_eur'} dir={sortDir} />
              </th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="bg-surface-container-lowest">
            {sorted.map((lead, idx) => {
              const name = displayName(lead);
              const last = lastEvent(lead);
              const pConv = conversionProb(lead.pipeline_status);
              const valEur = estimatedEur(lead);

              return (
                <tr
                  key={lead.id}
                  className="transition-colors hover:bg-surface-container-low"
                  style={
                    idx !== 0
                      ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.12)' }
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
                                ? '#006a37'
                                : pConv >= 0.2
                                  ? '#fdbb31'
                                  : '#aaaead',
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
    </div>
  );
}
