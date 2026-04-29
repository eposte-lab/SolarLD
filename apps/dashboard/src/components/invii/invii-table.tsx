'use client';

/**
 * InviiTable — client wrapper for the "Storico invii" table on /invii.
 * Sort applies to the current paginated page (the page itself does
 * server-side fetching upstream).
 */

import { ArrowUpRight, Check } from 'lucide-react';
import Link from 'next/link';

import { SortableTh } from '@/components/ui/sortable-th';
import { useSortableData } from '@/hooks/use-sortable-data';
import { cn, relativeTime } from '@/lib/utils';
import type { CampaignWithLeadEngagement } from '@/types/db';

const CHANNEL_STYLES: Record<string, string> = {
  email: 'bg-primary-container/60 text-on-primary-container',
  postal: 'bg-tertiary-container/60 text-on-tertiary-container',
  whatsapp: 'bg-surface-container-highest text-on-surface',
};
const CHANNEL_LABELS: Record<string, string> = {
  email: 'Email',
  postal: 'Postale',
  whatsapp: 'WhatsApp',
};

const STATUS_STYLES: Record<string, string> = {
  pending: 'bg-surface-container-high text-on-surface-variant',
  sent: 'bg-surface-container-highest text-on-surface',
  delivered: 'bg-primary-container text-on-primary-container',
  failed: 'bg-secondary-container text-on-secondary-container',
  cancelled: 'bg-surface-container text-on-surface-variant opacity-70',
};
const STATUS_LABELS: Record<string, string> = {
  pending: 'In coda',
  sent: 'Inviato',
  delivered: 'Consegnato',
  failed: 'Fallito',
  cancelled: 'Cancellato',
};
const STATUS_ORDER: Record<string, number> = {
  pending: 0,
  sent: 1,
  delivered: 2,
  failed: 3,
  cancelled: 4,
};

type SortKey =
  | 'sent_at'
  | 'step'
  | 'channel'
  | 'subject'
  | 'status'
  | 'delivered'
  | 'opened'
  | 'clicked'
  | 'cost';

export function InviiTable({ rows }: { rows: CampaignWithLeadEngagement[] }) {
  const { sorted, sortKey, sortDir, requestSort } = useSortableData<
    CampaignWithLeadEngagement,
    SortKey
  >(rows, (c, key) => {
    switch (key) {
      case 'sent_at':
        return c.sent_at ?? c.created_at ?? null;
      case 'step':
        return c.sequence_step ?? 0;
      case 'channel':
        return CHANNEL_LABELS[c.channel] ?? c.channel;
      case 'subject':
        return c.email_subject ?? c.template_id ?? '';
      case 'status':
        return STATUS_ORDER[c.status] ?? 99;
      case 'delivered':
        return c.leads?.outreach_delivered_at || c.status === 'delivered' ? 1 : 0;
      case 'opened':
        return c.leads?.outreach_opened_at ? 1 : 0;
      case 'clicked':
        return c.leads?.outreach_clicked_at ? 1 : 0;
      case 'cost':
        return c.cost_cents ?? 0;
    }
  });

  return (
    <div className="overflow-hidden rounded-lg bg-surface-container-low">
      <table className="w-full text-sm">
        <thead>
          <tr>
            <SortableTh sortKey="sent_at" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Data invio</SortableTh>
            <SortableTh sortKey="step" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Step</SortableTh>
            <SortableTh sortKey="channel" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Canale</SortableTh>
            <SortableTh sortKey="subject" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Subject / Template</SortableTh>
            <SortableTh sortKey="status" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Stato invio</SortableTh>
            <SortableTh sortKey="delivered" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Consegnato</SortableTh>
            <SortableTh sortKey="opened" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Aperto</SortableTh>
            <SortableTh sortKey="clicked" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Click</SortableTh>
            <SortableTh sortKey="cost" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3" align="right">Costo</SortableTh>
            <th className="px-5 py-3" />
          </tr>
        </thead>
        <tbody className="bg-surface-container-lowest">
          {sorted.map((c, idx) => (
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
                <span
                  className={cn(
                    'inline-flex rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest',
                    CHANNEL_STYLES[c.channel] ?? 'bg-surface-container text-on-surface-variant',
                  )}
                >
                  {CHANNEL_LABELS[c.channel] ?? c.channel}
                </span>
              </td>
              <td className="max-w-xs truncate px-5 py-3 text-xs font-medium text-on-surface">
                {c.email_subject ?? c.template_id ?? (
                  <span className="text-on-surface-variant">—</span>
                )}
              </td>
              <td className="px-5 py-3">
                <span
                  className={cn(
                    'inline-flex rounded-md px-2 py-0.5 text-xs font-medium',
                    STATUS_STYLES[c.status] ?? 'bg-surface-container text-on-surface-variant',
                  )}
                >
                  {STATUS_LABELS[c.status] ?? c.status}
                </span>
              </td>
              <td className="px-5 py-3 text-xs">
                {c.leads?.outreach_delivered_at || c.status === 'delivered' ? (
                  <Check size={14} strokeWidth={2.5} className="text-primary" aria-label="Consegnato" />
                ) : (
                  <span className="text-on-surface-variant">—</span>
                )}
              </td>
              <td className="px-5 py-3 text-xs">
                {c.leads?.outreach_opened_at ? (
                  <Check size={14} strokeWidth={2.5} className="text-primary" aria-label="Aperto" />
                ) : (
                  <span className="text-on-surface-variant">—</span>
                )}
              </td>
              <td className="px-5 py-3 text-xs">
                {c.leads?.outreach_clicked_at ? (
                  <Check size={14} strokeWidth={2.5} className="text-primary" aria-label="Cliccato" />
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
                <div className="flex items-center justify-end gap-3">
                  <Link
                    href={`/invii/${c.id}`}
                    className="group/link inline-flex items-center gap-1 text-xs font-semibold text-on-surface-variant hover:text-primary hover:underline"
                  >
                    dettaglio
                    <ArrowUpRight
                      size={11}
                      strokeWidth={2.5}
                      className="transition-transform group-hover/link:translate-x-0.5 group-hover/link:-translate-y-0.5"
                      aria-hidden
                    />
                  </Link>
                  <Link
                    href={`/leads/${c.lead_id}`}
                    className="group/link inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline"
                  >
                    lead
                    <ArrowUpRight
                      size={11}
                      strokeWidth={2.5}
                      className="transition-transform group-hover/link:translate-x-0.5 group-hover/link:-translate-y-0.5"
                      aria-hidden
                    />
                  </Link>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
