'use client';

/**
 * DomainHealthTable — sortable client wrapper around the "Salute dei domini"
 * table on /deliverability.
 */

import { BadgeStatus } from '@/components/ui/badge-status';
import { SortableTh } from '@/components/ui/sortable-th';
import { useSortableData } from '@/hooks/use-sortable-data';
import type { DomainHealthRow } from '@/lib/data/deliverability';

const STATUS_ORDER: Record<string, number> = {
  active: 0,
  paused: 1,
  inactive: 2,
};

type SortKey =
  | 'domain'
  | 'purpose'
  | 'spf'
  | 'dkim'
  | 'dmarc'
  | 'tracking'
  | 'cap'
  | 'status';

function DnsCheck({ ok }: { ok: boolean }) {
  return ok ? (
    <span className="inline-block h-2 w-2 rounded-full bg-success" title="Verificato" />
  ) : (
    <span className="inline-block h-2 w-2 rounded-full bg-error" title="Non verificato" />
  );
}

function StatusChip({ status }: { status: 'active' | 'paused' | 'inactive' }) {
  if (status === 'active') return <BadgeStatus tone="success" label="Attivo" />;
  if (status === 'paused') return <BadgeStatus tone="warning" label="Sospeso" />;
  return <BadgeStatus tone="neutral" label="Inattivo" dotless />;
}

export function DomainHealthTable({ rows }: { rows: DomainHealthRow[] }) {
  const { sorted, sortKey, sortDir, requestSort } = useSortableData<
    DomainHealthRow,
    SortKey
  >(rows, (d, key) => {
    switch (key) {
      case 'domain':
        return d.domain;
      case 'purpose':
        return d.purpose;
      case 'spf':
        return d.spf_verified_at ? 1 : 0;
      case 'dkim':
        return d.dkim_verified_at ? 1 : 0;
      case 'dmarc':
        return d.dmarc_verified_at ? 1 : 0;
      case 'tracking':
        return d.tracking_cname_verified_at ? 1 : 0;
      case 'cap':
        return d.daily_soft_cap;
      case 'status':
        return STATUS_ORDER[d.status] ?? 99;
    }
  });

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-surface-container-high">
            <SortableTh sortKey="domain" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-3">Dominio</SortableTh>
            <SortableTh sortKey="purpose" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-3">Scopo</SortableTh>
            <SortableTh sortKey="spf" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-3" align="center">SPF</SortableTh>
            <SortableTh sortKey="dkim" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-3" align="center">DKIM</SortableTh>
            <SortableTh sortKey="dmarc" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-3" align="center">DMARC</SortableTh>
            <SortableTh sortKey="tracking" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-3" align="center">Tracking</SortableTh>
            <SortableTh sortKey="cap" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-3" align="right">Cap/day</SortableTh>
            <SortableTh sortKey="status" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-3" align="right">Stato</SortableTh>
          </tr>
        </thead>
        <tbody>
          {sorted.map((d) => (
            <tr
              key={d.id}
              className="border-b border-surface-container-low last:border-0"
            >
              <td className="py-3">
                <span className="font-mono text-xs font-semibold text-on-surface">
                  {d.domain}
                </span>
                {d.pause_reason && d.status === 'paused' && (
                  <p className="mt-0.5 text-[10px] text-on-surface-variant">
                    {d.pause_reason.replace(/_/g, ' ')}
                  </p>
                )}
              </td>
              <td className="py-3">
                <span className="text-xs text-on-surface-variant">
                  {d.purpose === 'brand' ? 'Brand' : 'Outreach'}
                </span>
              </td>
              <td className="py-3 text-center">
                <DnsCheck ok={!!d.spf_verified_at} />
              </td>
              <td className="py-3 text-center">
                <DnsCheck ok={!!d.dkim_verified_at} />
              </td>
              <td className="py-3 text-center">
                <DnsCheck ok={!!d.dmarc_verified_at} />
              </td>
              <td className="py-3 text-center">
                <DnsCheck ok={!!d.tracking_cname_verified_at} />
              </td>
              <td className="py-3 text-right tabular-nums text-on-surface-variant">
                {d.daily_soft_cap.toLocaleString('it-IT')}
              </td>
              <td className="py-3 text-right">
                <StatusChip status={d.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
