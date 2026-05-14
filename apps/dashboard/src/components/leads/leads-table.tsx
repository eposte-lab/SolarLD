'use client';

/**
 * LeadsTable — client wrapper around the leads list table.
 *
 * Owned by the server component at `app/(dashboard)/leads/page.tsx`,
 * which prefetches `rows` server-side and passes them in. This wrapper
 * adds click-to-sort headers via `useSortableData`.
 *
 * AI overlay: when a lead has a `lead_imminence_predictions` row for
 * today (passed in via `predictionsByLead`), the row gets:
 *   - a primary-tinted left border + faint highlight
 *   - an "AI" badge next to the name
 *   - an expandable detail row with reasons / suggested action /
 *     talking points / per-sub-score breakdown
 *
 * The expand state is local; collapsed is the default so the existing
 * dense layout is preserved for operators who don't want the overlay.
 *
 * Note: sort applies to the current paginated page only (server-paginated
 * upstream). Cross-page sort would need to push the order into the URL
 * and back into the SQL query — out of scope for the UI-level patch.
 */

import Link from 'next/link';
import { Fragment, useState } from 'react';

import { EngagementScoreChip } from '@/components/ui/engagement-score-chip';
import { FollowUpStateChip } from '@/components/ui/follow-up-state-chip';
import { SortableTh } from '@/components/ui/sortable-th';
import { StatusChip, TierChip } from '@/components/ui/status-chip';
import { useSortableData } from '@/hooks/use-sortable-data';
import { followUpState } from '@/lib/data/followup-state';
import { cn, daysSince, relativeTime } from '@/lib/utils';
import type { ImminencePrediction } from '@/lib/data/imminence';
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
  | 'followup'
  | 'status'
  | 'last_touch'
  | 'imminence';

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

const ACTION_LABEL: Record<string, string> = {
  call_now: 'Chiama subito',
  call_today: 'Chiama oggi',
  send_followup: 'Manda follow-up',
  wait_24h: 'Aspetta 24h',
};

const TIME_LABEL: Record<string, string> = {
  morning_9_11: 'Mattina (9-11)',
  afternoon_14_17: 'Pomeriggio (14-17)',
  now: 'Adesso',
};

const CHANNEL_LABEL: Record<string, string> = {
  phone: 'Telefono',
  email: 'Email',
  whatsapp: 'WhatsApp',
};

export function LeadsTable({
  rows,
  pipelineLabels,
  predictionsByLead,
}: {
  rows: LeadListRow[];
  pipelineLabels: string[];
  predictionsByLead?: Map<string, ImminencePrediction>;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

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
      case 'followup':
        // Lower weight = more urgent (manual=0, interessato=1, ...,
        // conversazione=-1). Multiply by -1 so DESC sort surfaces
        // "Manuale" first.
        return -followUpState(lead).weight;
      case 'status':
        return lead.pipeline_status ?? '';
      case 'last_touch':
        return lastTouchOf(lead);
      case 'imminence':
        return predictionsByLead?.get(lead.id)?.imminence_score ?? null;
    }
  });

  // Default sort: predicted leads first (by imminence DESC), then by score.
  // Only re-sort if the user hasn't explicitly chosen a column.
  const finalSorted =
    sortKey === null && predictionsByLead && predictionsByLead.size > 0
      ? [...sorted].sort((a, b) => {
          const pa = predictionsByLead.get(a.id)?.imminence_score ?? -1;
          const pb = predictionsByLead.get(b.id)?.imminence_score ?? -1;
          return pb - pa;
        })
      : sorted;

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="overflow-hidden rounded-lg bg-surface-container-low">
      <table className="w-full text-sm">
        <thead>
          <tr>
            <SortableTh sortKey="name" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Lead</SortableTh>
            <SortableTh sortKey="type" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Tipo</SortableTh>
            <SortableTh sortKey="comune" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Comune</SortableTh>
            <SortableTh sortKey="kwp" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3" align="right">kW</SortableTh>
            <SortableTh sortKey="score" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3" align="right">Score</SortableTh>
            <SortableTh sortKey="tier" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Tier</SortableTh>
            <SortableTh sortKey="engagement" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Engagement</SortableTh>
            <SortableTh sortKey="followup" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Follow-up</SortableTh>
            <SortableTh sortKey="status" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Stato</SortableTh>
            <SortableTh sortKey="last_touch" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Ultimo tocco</SortableTh>
            <th className="px-5 py-3" />
          </tr>
        </thead>
        <tbody className="bg-surface-container-lowest">
          {finalSorted.map((lead, idx) => {
            const name = leadName(lead);
            const lastTouch = lastTouchOf(lead);
            const age = daysSince(lead.outreach_sent_at);
            const prediction = predictionsByLead?.get(lead.id);
            const isAi = !!prediction;
            const isExpanded = expanded.has(lead.id);
            // Manual handoff (engagement >= 61) takes precedence over the
            // AI-imminence overlay — both compete for the row tint, but
            // "system stopped, call this lead now" is more urgent than
            // "AI suggests calling this lead today".
            const isManual = followUpState(lead).kind === 'manual';
            return (
              <Fragment key={lead.id}>
                <tr
                  className={cn(
                    'transition-colors hover:bg-surface-container-low',
                    !isManual && isAi && 'bg-primary/5 hover:bg-primary/10',
                    isManual && 'bg-error-container/15 hover:bg-error-container/25',
                  )}
                  style={
                    idx !== 0
                      ? {
                          boxShadow: isManual
                            ? 'inset 4px 0 0 var(--md-sys-color-error), inset 0 1px 0 rgba(170,174,173,0.15)'
                            : isAi
                              ? 'inset 4px 0 0 var(--md-sys-color-primary), inset 0 1px 0 rgba(170,174,173,0.15)'
                              : 'inset 0 1px 0 rgba(170,174,173,0.15)',
                        }
                      : isManual
                        ? { boxShadow: 'inset 4px 0 0 var(--md-sys-color-error)' }
                        : isAi
                          ? { boxShadow: 'inset 4px 0 0 var(--md-sys-color-primary)' }
                          : undefined
                  }
                >
                  <td className="px-5 py-4 font-semibold text-on-surface">
                    <div className="flex items-center gap-2">
                      <span>{name}</span>
                      {isAi && (
                        <button
                          type="button"
                          onClick={() => toggle(lead.id)}
                          className="inline-flex items-center gap-1 rounded-full bg-primary/15 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-primary hover:bg-primary/25"
                          title="Lead consigliato dall'agente AI — clicca per vedere perché"
                        >
                          <svg
                            viewBox="0 0 24 24"
                            className="h-2.5 w-2.5"
                            fill="currentColor"
                            aria-hidden
                          >
                            <path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z" />
                          </svg>
                          AI {prediction.imminence_score}
                          <svg
                            viewBox="0 0 24 24"
                            className={cn(
                              'h-2.5 w-2.5 transition-transform',
                              isExpanded && 'rotate-180',
                            )}
                            fill="currentColor"
                            aria-hidden
                          >
                            <path d="M7 10l5 5 5-5z" />
                          </svg>
                        </button>
                      )}
                    </div>
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
                    <FollowUpStateChip row={lead} />
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
                {isAi && isExpanded && (
                  <tr className="bg-primary/5">
                    <td colSpan={11} className="px-8 py-4">
                      <ImminenceDetail prediction={prediction} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ImminenceDetail({ prediction }: { prediction: ImminencePrediction }) {
  const action = prediction.suggested_action
    ? ACTION_LABEL[prediction.suggested_action] ?? prediction.suggested_action
    : null;
  const channel = prediction.suggested_channel
    ? CHANNEL_LABEL[prediction.suggested_channel] ?? prediction.suggested_channel
    : null;
  const time = prediction.best_time_to_contact
    ? TIME_LABEL[prediction.best_time_to_contact] ?? prediction.best_time_to_contact
    : null;

  return (
    <div className="space-y-3">
      {prediction.primary_reasons.length > 0 && (
        <div>
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Perché chiamarlo oggi
          </p>
          <ul className="space-y-1 text-sm text-on-surface">
            {prediction.primary_reasons.map((r, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-primary">•</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {(action || channel || time) && (
        <div className="flex flex-wrap gap-2 pt-1 text-xs">
          {action && (
            <span className="rounded-full bg-primary/15 px-3 py-1 font-semibold text-primary">
              {action}
            </span>
          )}
          {channel && (
            <span className="rounded-full bg-surface-container-high px-3 py-1 text-on-surface-variant">
              Canale: {channel}
            </span>
          )}
          {time && (
            <span className="rounded-full bg-surface-container-high px-3 py-1 text-on-surface-variant">
              Quando: {time}
            </span>
          )}
        </div>
      )}

      {prediction.talking_points.length > 0 && (
        <div>
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Argomenti per aprire la conversazione
          </p>
          <ul className="space-y-0.5 text-xs text-on-surface-variant">
            {prediction.talking_points.map((t, i) => (
              <li key={i}>→ {t}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex gap-3 pt-1 text-[10px] text-on-surface-variant">
        <span>Comportamentale {prediction.behavioral_score}</span>
        <span>·</span>
        <span>Temporale {prediction.temporal_score}</span>
        <span>·</span>
        <span>Contestuale {prediction.contextual_score}</span>
        <span>·</span>
        <span>Comparativo {prediction.comparative_score}</span>
      </div>
    </div>
  );
}
