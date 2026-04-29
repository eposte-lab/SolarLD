'use client';

/**
 * ProviderSpendTable — sortable client wrapper for the "Spesa per provider"
 * breakdown on /analytics.
 */

import { SortableTh } from '@/components/ui/sortable-th';
import { useSortableData } from '@/hooks/use-sortable-data';
import type { SpendByProviderRow } from '@/lib/data/analytics';
import { formatEurPlain, formatNumber } from '@/lib/utils';

type SortKey = 'provider' | 'calls' | 'errors' | 'cost';

export function ProviderSpendTable({ rows }: { rows: SpendByProviderRow[] }) {
  const { sorted, sortKey, sortDir, requestSort } = useSortableData<
    SpendByProviderRow,
    SortKey
  >(rows, (row, key) => {
    switch (key) {
      case 'provider':
        return row.provider;
      case 'calls':
        return row.calls;
      case 'errors':
        return row.errors;
      case 'cost':
        return row.cost_cents;
    }
  });

  return (
    <div className="overflow-hidden rounded-lg bg-surface-container-low">
      <table className="w-full text-sm">
        <thead>
          <tr>
            <SortableTh sortKey="provider" active={sortKey} dir={sortDir} onSort={requestSort} className="px-4 py-3">Provider</SortableTh>
            <SortableTh sortKey="calls" active={sortKey} dir={sortDir} onSort={requestSort} className="px-4 py-3" align="right">Chiamate</SortableTh>
            <SortableTh sortKey="errors" active={sortKey} dir={sortDir} onSort={requestSort} className="px-4 py-3" align="right">Errori</SortableTh>
            <SortableTh sortKey="cost" active={sortKey} dir={sortDir} onSort={requestSort} className="px-4 py-3" align="right">Costo</SortableTh>
          </tr>
        </thead>
        <tbody className="bg-surface-container-lowest">
          {sorted.map((row, idx) => (
            <tr
              key={row.provider}
              style={
                idx !== 0
                  ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }
                  : undefined
              }
            >
              <td className="px-4 py-3 font-mono text-xs text-on-surface">
                {row.provider}
              </td>
              <td className="px-4 py-3 text-right tabular-nums">
                {formatNumber(row.calls)}
              </td>
              <td className="px-4 py-3 text-right tabular-nums">
                {row.errors > 0 ? (
                  <span className="text-secondary">
                    {formatNumber(row.errors)}
                  </span>
                ) : (
                  <span className="text-on-surface-variant">—</span>
                )}
              </td>
              <td className="px-4 py-3 text-right font-semibold tabular-nums">
                {formatEurPlain(row.cost_cents / 100)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
