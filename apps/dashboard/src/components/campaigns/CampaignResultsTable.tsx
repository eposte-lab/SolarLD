'use client';

/**
 * CampaignResultsTable — sortable A/B results breakdown for a single
 * acquisition campaign. Used inside the "Risultati" tab of
 * /campaigns/[id].
 */

import { SortableTh } from '@/components/ui/sortable-th';
import { useSortableData } from '@/hooks/use-sortable-data';
import { cn } from '@/lib/utils';
import type { CampaignResultRow } from '@/lib/data/campaign-results';

type SortKey =
  | 'variant'
  | 'province'
  | 'tier'
  | 'sent'
  | 'delivered'
  | 'opened'
  | 'clicked'
  | 'replied'
  | 'open_rate';

const TIER_ORDER: Record<string, number> = { hot: 3, warm: 2, cold: 1, rejected: 0 };

export function CampaignResultsTable({ rows }: { rows: CampaignResultRow[] }) {
  const { sorted, sortKey, sortDir, requestSort } = useSortableData<
    CampaignResultRow,
    SortKey
  >(rows, (row, key) => {
    switch (key) {
      case 'variant':
        return row.variant ?? 'Controllo';
      case 'province':
        return row.province ?? '';
      case 'tier':
        return row.score_tier ? TIER_ORDER[row.score_tier] ?? -1 : null;
      case 'sent':
        return row.sent;
      case 'delivered':
        return row.delivered;
      case 'opened':
        return row.opened;
      case 'clicked':
        return row.clicked;
      case 'replied':
        return row.replied;
      case 'open_rate':
        return row.sent > 0 ? row.opened / row.sent : null;
    }
  });

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/8">
            <SortableTh sortKey="variant" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-2 pr-4">Variante</SortableTh>
            <SortableTh sortKey="province" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-2 pr-4">Provincia</SortableTh>
            <SortableTh sortKey="tier" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-2 pr-4">Tier</SortableTh>
            <SortableTh sortKey="sent" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-2 pr-4">Inviati</SortableTh>
            <SortableTh sortKey="delivered" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-2 pr-4">Consegnati</SortableTh>
            <SortableTh sortKey="opened" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-2 pr-4">Aperti</SortableTh>
            <SortableTh sortKey="clicked" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-2 pr-4">Cliccati</SortableTh>
            <SortableTh sortKey="replied" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-2 pr-4">Risposte</SortableTh>
            <SortableTh sortKey="open_rate" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-2 pr-4">Open%</SortableTh>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {sorted.map((row, i) => {
            const openRate = row.sent > 0 ? ((row.opened / row.sent) * 100).toFixed(1) : '—';
            return (
              <tr key={i} className="hover:bg-white/3">
                <td className="py-2 pr-4 font-medium text-on-surface">
                  {row.variant ?? 'Controllo'}
                </td>
                <td className="py-2 pr-4 text-on-surface-variant">
                  {row.province ?? '—'}
                </td>
                <td className="py-2 pr-4 text-on-surface-variant">
                  {row.score_tier ?? '—'}
                </td>
                <td className="py-2 pr-4 tabular-nums">{row.sent}</td>
                <td className="py-2 pr-4 tabular-nums text-on-surface-variant">
                  {row.delivered}
                </td>
                <td className="py-2 pr-4 tabular-nums text-on-surface-variant">
                  {row.opened}
                </td>
                <td className="py-2 pr-4 tabular-nums text-on-surface-variant">
                  {row.clicked}
                </td>
                <td className="py-2 pr-4 tabular-nums text-on-surface-variant">
                  {row.replied}
                </td>
                <td
                  className={cn(
                    'py-2 pr-4 tabular-nums font-semibold',
                    row.opened / row.sent > 0.15
                      ? 'text-primary'
                      : 'text-on-surface-variant',
                  )}
                >
                  {openRate}
                  {openRate !== '—' ? '%' : ''}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
