'use client';

/**
 * InboxFleetTable — sortable client wrapper around the "Fleet inbox" table
 * on /deliverability.
 */

import { BadgeStatus } from '@/components/ui/badge-status';
import { SortableTh } from '@/components/ui/sortable-th';
import { useSortableData } from '@/hooks/use-sortable-data';
import { cn, relativeTime } from '@/lib/utils';
import type { InboxFleetRow, WarmupPhase } from '@/lib/data/deliverability';

const WARMUP_LABEL: Record<WarmupPhase, string> = {
  not_started: 'Non avviato',
  week_1: 'Ramp-up sett. 1',
  week_2: 'Ramp-up sett. 2',
  week_3: 'Ramp-up sett. 3',
  steady: 'Regime',
};

const WARMUP_COLOR: Record<WarmupPhase, string> = {
  not_started: 'bg-white/8 text-on-surface-variant',
  week_1: 'bg-primary/10 text-primary',
  week_2: 'bg-primary/15 text-primary',
  week_3: 'bg-primary/20 text-primary',
  steady: 'bg-success/15 text-success',
};

const WARMUP_ORDER: Record<WarmupPhase, number> = {
  not_started: 0,
  week_1: 1,
  week_2: 2,
  week_3: 3,
  steady: 4,
};

const STATUS_ORDER: Record<string, number> = {
  active: 0,
  paused: 1,
  inactive: 2,
};

type SortKey =
  | 'inbox'
  | 'domain'
  | 'phase'
  | 'usage'
  | 'smartlead'
  | 'last_sent'
  | 'status';

function StatusChip({ status }: { status: 'active' | 'paused' | 'inactive' }) {
  if (status === 'active') return <BadgeStatus tone="success" label="Attivo" />;
  if (status === 'paused') return <BadgeStatus tone="warning" label="Sospeso" />;
  return <BadgeStatus tone="neutral" label="Inattivo" dotless />;
}

function SmartleadScore({ score }: { score: number | null }) {
  if (score === null)
    return <span className="text-on-surface-variant text-xs">—</span>;
  const color =
    score >= 70
      ? 'text-success'
      : score >= 40
        ? 'text-primary'
        : 'text-error';
  return <span className={cn('font-semibold tabular-nums text-sm', color)}>{score.toFixed(0)}</span>;
}

function inboxStatusOf(inbox: InboxFleetRow): 'active' | 'paused' | 'inactive' {
  if (!inbox.active) return 'inactive';
  if (inbox.paused_until && inbox.paused_until > new Date().toISOString())
    return 'paused';
  return 'active';
}

function sentTodayOf(inbox: InboxFleetRow): number {
  const today = new Date().toISOString().slice(0, 10);
  return inbox.sent_date === today ? inbox.total_sent_today : 0;
}

export function InboxFleetTable({ rows }: { rows: InboxFleetRow[] }) {
  const { sorted, sortKey, sortDir, requestSort } = useSortableData<
    InboxFleetRow,
    SortKey
  >(rows, (inbox, key) => {
    switch (key) {
      case 'inbox':
        return inbox.display_name || inbox.email;
      case 'domain':
        return inbox.domain_name ?? '';
      case 'phase':
        return WARMUP_ORDER[inbox.warmup_phase] ?? 99;
      case 'usage': {
        const sent = sentTodayOf(inbox);
        const cap = inbox.effective_cap;
        return cap > 0 ? sent / cap : 0;
      }
      case 'smartlead':
        return inbox.smartlead_health_score;
      case 'last_sent':
        return inbox.last_sent_at;
      case 'status':
        return STATUS_ORDER[inboxStatusOf(inbox)] ?? 99;
    }
  });

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-surface-container-high">
            <SortableTh sortKey="inbox" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-3">Inbox</SortableTh>
            <SortableTh sortKey="domain" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-3">Dominio</SortableTh>
            <SortableTh sortKey="phase" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-3">Fase</SortableTh>
            <SortableTh sortKey="usage" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-3" align="right">Inviati / Cap</SortableTh>
            <SortableTh sortKey="smartlead" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-3" align="right">Smartlead</SortableTh>
            <SortableTh sortKey="last_sent" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-3" align="right">Ultimo invio</SortableTh>
            <SortableTh sortKey="status" active={sortKey} dir={sortDir} onSort={requestSort} className="pb-3" align="right">Stato</SortableTh>
          </tr>
        </thead>
        <tbody>
          {sorted.map((inbox) => {
            const sentToday = sentTodayOf(inbox);
            const cap = inbox.effective_cap;
            const pct = cap > 0 ? (sentToday / cap) * 100 : 0;
            const status = inboxStatusOf(inbox);
            return (
              <tr
                key={inbox.id}
                className="border-b border-surface-container-low last:border-0"
              >
                <td className="py-3">
                  <p className="text-xs font-semibold text-on-surface">
                    {inbox.display_name || inbox.email.split('@')[0]}
                  </p>
                  <p className="text-[10px] text-on-surface-variant">
                    {inbox.email}
                  </p>
                </td>
                <td className="py-3 text-xs text-on-surface-variant">
                  {inbox.domain_name ?? '—'}
                </td>
                <td className="py-3">
                  <span
                    className={cn(
                      'rounded-full px-2 py-0.5 text-[10px] font-semibold',
                      WARMUP_COLOR[inbox.warmup_phase],
                    )}
                  >
                    {WARMUP_LABEL[inbox.warmup_phase]}
                  </span>
                </td>
                <td className="py-3 text-right">
                  <div className="flex items-center justify-end gap-2">
                    <div className="h-1.5 w-20 overflow-hidden rounded-full bg-white/8">
                      <div
                        className={cn(
                          'h-full rounded-full transition-all',
                          pct >= 95
                            ? 'bg-error'
                            : pct >= 70
                              ? 'bg-primary'
                              : 'bg-success',
                        )}
                        style={{ width: `${Math.min(100, pct).toFixed(0)}%` }}
                      />
                    </div>
                    <span className="min-w-[60px] text-right tabular-nums text-xs text-on-surface-variant">
                      {sentToday} / {cap}
                    </span>
                  </div>
                </td>
                <td className="py-3 text-right">
                  <SmartleadScore score={inbox.smartlead_health_score} />
                </td>
                <td className="py-3 text-right text-xs text-on-surface-variant">
                  {inbox.last_sent_at ? relativeTime(inbox.last_sent_at) : '—'}
                </td>
                <td className="py-3 text-right">
                  <StatusChip status={status} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
