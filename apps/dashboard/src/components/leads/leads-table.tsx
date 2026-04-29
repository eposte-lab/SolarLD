'use client';

/**
 * LeadsTable — client wrapper around the leads list table.
 *
 * Owned by the server component at `app/(dashboard)/leads/page.tsx`,
 * which prefetches `rows` server-side and passes them in. This wrapper
 * adds click-to-sort headers via `useSortableData`.
 *
 * Note: sort applies to the current paginated page only (server-paginated
 * upstream). Cross-page sort would need to push the order into the URL
 * and back into the SQL query — out of scope for the UI-level patch.
 */

import Link from 'next/link';

import { EngagementScoreChip } from '@/components/ui/engagement-score-chip';
import { SortableTh } from '@/components/ui/sortable-th';
import { StatusChip, TierChip } from '@/components/ui/status-chip';
import { useSortableData } from '@/hooks/use-sortable-data';
import { daysSince, relativeTime } from '@/lib/utils';
import type { LeadListRow } from '@/types/db';

const TIER_ORDER: Record<string, number> = {
  hot: 3,
  warm: 2,
  cold: 1,
  rejected: 0,
};

type SortKey =
  | 'name'
  | 'type'
  | 'comune'
  | 'kwp'
  | 'score'
  | 'tier'
  | 'engagement'
  | 'status'
  | 'last_touch';

function leadName(lead: LeadListRow): string {
  return (
    lead.subjects?.business_name ||
    [lead.subjects?.owner_first_name, lead.subjects?.owner_last_name]
      .filter(Boolean)
      .join(' ') ||
    '—'
  );
}

function lastTouchOf(lead: LeadListRow): string {
  return (
    lead.dashboard_visited_at ||
    lead.outreach_opened_at ||
    lead.outreach_sent_at ||
    lead.created_at
  );
}

export function LeadsTable({
  rows,
  pipelineLabels,
}: {
  rows: LeadListRow[];
  pipelineLabels: string[];
}) {
  const { sorted, sortKey, sortDir, requestSort } = useSortableData<
    LeadListRow,
    SortKey
  >(rows, (lead, key) => {
    switch (key) {
      case 'name':
        return leadName(lead);
      case 'type':
        return lead.subjects?.type ?? '';
      case 'comune':
        return lead.roofs?.comune ?? '';
      case 'kwp':
        return lead.roofs?.estimated_kwp ?? null;
      case 'score':
        return lead.score;
      case 'tier':
        return TIER_ORDER[lead.score_tier] ?? 0;
      case 'engagement':
        return lead.engagement_score ?? null;
      case 'status':
        return lead.pipeline_status ?? '';
      case 'last_touch':
        return lastTouchOf(lead);
    }
  });

  return (
    <div className="overflow-hidden rounded-lg bg-surface-container-low">
      <table className="w-full text-sm">
        <thead>
          <tr>
            <SortableTh sortKey="name" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Lead</SortableTh>
            <SortableTh sortKey="type" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Tipo</SortableTh>
            <SortableTh sortKey="comune" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Comune</SortableTh>
            <SortableTh sortKey="kwp" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3" align="right">kWp</SortableTh>
            <SortableTh sortKey="score" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3" align="right">Score</SortableTh>
            <SortableTh sortKey="tier" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Tier</SortableTh>
            <SortableTh sortKey="engagement" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Engagement</SortableTh>
            <SortableTh sortKey="status" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Stato</SortableTh>
            <SortableTh sortKey="last_touch" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Ultimo tocco</SortableTh>
            <th className="px-5 py-3" />
          </tr>
        </thead>
        <tbody className="bg-surface-container-lowest">
          {sorted.map((lead, idx) => {
            const name = leadName(lead);
            const lastTouch = lastTouchOf(lead);
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
                  <StatusChip
                    status={lead.pipeline_status}
                    pipelineLabels={pipelineLabels}
                  />
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
  );
}
