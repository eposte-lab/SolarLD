'use client';

/**
 * AuditLogTable — sortable client wrapper for the privacy audit log.
 */

import { SortableTh } from '@/components/ui/sortable-th';
import { useSortableData } from '@/hooks/use-sortable-data';
import { relativeTime } from '@/lib/utils';
import type { AuditLogRow } from '@/types/db';

const ACTION_LABELS: Record<string, string> = {
  'lead.feedback_updated': 'Feedback aggiornato',
  'lead.follow_up_sent': 'Follow-up AI inviato',
  'lead.deleted': 'Lead eliminato (GDPR)',
  'config.updated': 'Configurazione aggiornata',
  'webhook.created': 'Webhook creato',
  'webhook.updated': 'Webhook aggiornato',
  'webhook.deleted': 'Webhook eliminato',
  'webhook.rotated': 'Secret webhook ruotato',
};

const TABLE_LABELS: Record<string, string> = {
  leads: 'Lead',
  campaigns: 'Campagna',
  tenants: 'Tenant',
  crm_webhook_subscriptions: 'Webhook',
};

type SortKey = 'at' | 'action' | 'target' | 'actor';

function targetLabelOf(row: AuditLogRow): string {
  if (row.target_table && row.target_id) {
    return `${TABLE_LABELS[row.target_table] ?? row.target_table} ${row.target_id.slice(0, 8)}…`;
  }
  if (row.target_table) {
    return TABLE_LABELS[row.target_table] ?? row.target_table;
  }
  return '—';
}

export function AuditLogTable({ rows }: { rows: AuditLogRow[] }) {
  const { sorted, sortKey, sortDir, requestSort } = useSortableData<
    AuditLogRow,
    SortKey
  >(rows, (row, key) => {
    switch (key) {
      case 'at':
        return row.at;
      case 'action':
        return ACTION_LABELS[row.action] ?? row.action;
      case 'target':
        return targetLabelOf(row);
      case 'actor':
        return row.actor_user_id ?? '';
    }
  });

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr>
            <SortableTh sortKey="at" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Quando</SortableTh>
            <SortableTh sortKey="action" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Azione</SortableTh>
            <SortableTh sortKey="target" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Oggetto</SortableTh>
            <SortableTh sortKey="actor" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Attore</SortableTh>
            <th className="px-5 py-3 text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Dettagli
            </th>
          </tr>
        </thead>
        <tbody className="bg-surface-container-lowest">
          {sorted.map((row, idx) => {
            const actionLabel = ACTION_LABELS[row.action] ?? row.action;
            const isDestructive = row.action.includes('deleted');
            const targetLabel = targetLabelOf(row);
            return (
              <tr
                key={String(row.id)}
                style={
                  idx !== 0
                    ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }
                    : undefined
                }
              >
                <td className="whitespace-nowrap px-5 py-3 text-xs text-on-surface-variant">
                  {relativeTime(row.at)}
                </td>
                <td className="px-5 py-3">
                  <span
                    className={
                      isDestructive
                        ? 'font-semibold text-error'
                        : 'font-semibold text-on-surface'
                    }
                  >
                    {actionLabel}
                  </span>
                </td>
                <td className="px-5 py-3 font-mono text-xs text-on-surface-variant">
                  {targetLabel}
                </td>
                <td className="px-5 py-3 font-mono text-xs text-on-surface-variant">
                  {row.actor_user_id ? row.actor_user_id.slice(0, 8) + '…' : 'system'}
                </td>
                <td className="max-w-xs px-5 py-3 text-xs text-on-surface-variant">
                  {row.diff ? (
                    <span className="font-mono">
                      {Object.entries(row.diff)
                        .filter(([, v]) => v !== null && v !== undefined)
                        .map(([k, v]) => `${k}: ${String(v)}`)
                        .join(' · ')
                        .slice(0, 120)}
                    </span>
                  ) : (
                    '—'
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
