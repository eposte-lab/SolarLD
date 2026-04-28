/**
 * HotLeadsWidget — overview-page "caldi senza risposta" surface.
 *
 * Reads from ``listHotLeadsAwaitingResponse`` (Sprint 8 Fase C.2):
 * leads whose ``engagement_score`` is ≥ 70 AND who triggered a portal
 * event in the last 24h AND who have NOT yet been claimed in the
 * pipeline (engaged / whatsapp / appointment / closed_*).
 *
 * Distinct from ``HotLeadsNow`` (which queries portal_events for raw
 * activity in the last 60 minutes): this widget is the **call list**
 * — the operator opens the dashboard and sees the 5 leads who are
 * hot AND haven't been touched yet.
 *
 * Server component — no client interactivity needed beyond Link
 * navigation. Refreshes on every dashboard render via
 * ``force-dynamic`` upstream.
 */

import Link from 'next/link';

import { BentoCard } from '@/components/ui/bento-card';
import { EngagementScoreChip } from '@/components/ui/engagement-score-chip';
import { listHotLeadsAwaitingResponse } from '@/lib/data/leads';
import { relativeTime } from '@/lib/utils';

export async function HotLeadsWidget({
  limit = 5,
  sinceHours = 24,
  minScore = 70,
}: {
  limit?: number;
  sinceHours?: number;
  minScore?: number;
} = {}) {
  const rows = await listHotLeadsAwaitingResponse({
    limit,
    sinceHours,
    minScore,
  });

  return (
    <BentoCard>
      <header className="mb-3 flex items-end justify-between">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Priorità chiamate
          </p>
          <h3 className="font-headline text-lg font-bold tracking-tighter">
            Caldi senza risposta
          </h3>
          <p className="mt-0.5 text-xs text-on-surface-variant">
            Engagement ≥ {minScore}, attivi nelle ultime {sinceHours}h, ancora
            da contattare.
          </p>
        </div>
        <Link
          href="/leads?mode=hot"
          className="shrink-0 text-xs font-semibold text-primary hover:underline"
        >
          Tutti →
        </Link>
      </header>

      {rows.length === 0 ? (
        <div className="rounded-lg bg-surface-container-low p-6 text-center">
          <p className="text-xs text-on-surface-variant">
            Nessun lead caldo senza risposta. Manda un nuovo round di outreach
            per riscaldare la pipeline.
          </p>
        </div>
      ) : (
        <ul className="divide-y divide-outline-variant">
          {rows.map((lead) => {
            const name =
              lead.subjects?.business_name ||
              [lead.subjects?.owner_first_name, lead.subjects?.owner_last_name]
                .filter(Boolean)
                .join(' ') ||
              lead.public_slug ||
              lead.id.slice(0, 8);
            const lastActivity =
              lead.last_portal_event_at ||
              lead.engagement_score_updated_at ||
              lead.created_at;
            return (
              <li
                key={lead.id}
                className="flex items-center justify-between gap-3 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <Link
                    href={`/leads/${lead.id}`}
                    className="block truncate font-medium text-on-surface hover:underline"
                  >
                    {name}
                  </Link>
                  <p className="text-[11px] text-on-surface-variant">
                    {lead.roofs?.comune ?? '—'} ·{' '}
                    <span className="opacity-80">
                      ultimo evento {relativeTime(lastActivity)}
                    </span>
                  </p>
                </div>
                <EngagementScoreChip
                  score={lead.engagement_score}
                  updatedAt={lead.engagement_score_updated_at}
                />
              </li>
            );
          })}
        </ul>
      )}
    </BentoCard>
  );
}
