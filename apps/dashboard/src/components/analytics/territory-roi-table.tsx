'use client';

/**
 * TerritoryRoiTable — sortable client wrapper for the "ROI per territorio"
 * table on /analytics.
 */

import { SortableTh } from '@/components/ui/sortable-th';
import { useSortableData } from '@/hooks/use-sortable-data';
import type { TerritoryRoiRow } from '@/lib/data/analytics';
import { formatEurPlain, formatNumber } from '@/lib/utils';

type SortKey =
  | 'territory'
  | 'leads_total'
  | 'leads_hot'
  | 'avg_score'
  | 'signed'
  | 'contract_value';

export function TerritoryRoiTable({ rows }: { rows: TerritoryRoiRow[] }) {
  const { sorted, sortKey, sortDir, requestSort } = useSortableData<
    TerritoryRoiRow,
    SortKey
  >(rows, (row, key) => {
    switch (key) {
      case 'territory':
        return row.territory_name;
      case 'leads_total':
        return row.leads_total;
      case 'leads_hot':
        return row.leads_hot;
      case 'avg_score':
        return row.leads_total > 0 ? row.avg_score : null;
      case 'signed':
        return row.signed;
      case 'contract_value':
        return row.contract_value_eur;
    }
  });

  return (
    <div className="overflow-hidden rounded-lg bg-surface-container-low">
      <table className="w-full text-sm">
        <thead>
          <tr>
            <SortableTh sortKey="territory" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Territorio</SortableTh>
            <SortableTh sortKey="leads_total" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3" align="right">Lead totali</SortableTh>
            <SortableTh sortKey="leads_hot" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3" align="right">Hot</SortableTh>
            <SortableTh sortKey="avg_score" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3" align="right">Score medio</SortableTh>
            <SortableTh sortKey="signed" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3" align="right">Firmati</SortableTh>
            <SortableTh sortKey="contract_value" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3" align="right">Valore contratti</SortableTh>
          </tr>
        </thead>
        <tbody className="bg-surface-container-lowest">
          {sorted.map((row, idx) => (
            <tr
              key={row.territory_id}
              style={
                idx !== 0
                  ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }
                  : undefined
              }
            >
              <td className="px-5 py-4 font-semibold text-on-surface">
                {row.territory_name}
              </td>
              <td className="px-5 py-4 text-right tabular-nums">
                {formatNumber(row.leads_total)}
              </td>
              <td className="px-5 py-4 text-right tabular-nums">
                {row.leads_hot > 0 ? (
                  <span className="font-semibold text-secondary">
                    {formatNumber(row.leads_hot)}
                  </span>
                ) : (
                  <span className="text-on-surface-variant">0</span>
                )}
              </td>
              <td className="px-5 py-4 text-right font-headline font-bold tabular-nums">
                {row.leads_total > 0 ? row.avg_score.toFixed(1) : '—'}
              </td>
              <td className="px-5 py-4 text-right tabular-nums">
                {formatNumber(row.signed)}
              </td>
              <td className="px-5 py-4 text-right font-semibold tabular-nums text-primary">
                {formatEurPlain(row.contract_value_eur)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
