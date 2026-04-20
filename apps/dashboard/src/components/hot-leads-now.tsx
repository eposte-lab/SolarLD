/**
 * HotLeadsNow — "caldi adesso" widget.
 *
 * Answers the operator question: *who is looking at their dossier right
 * now and should I call them?* Re-queries ``portal_events`` for the
 * last 60 minutes, groups by lead, surfaces the top 5.
 *
 * This is the **live** companion to the nightly ``engagement_score``
 * rollup: the rollup answers "who's been hot this month", this widget
 * answers "who's hot this hour". Both are surfaced so the operator
 * can triage (score for the queue, this for the call).
 *
 * Server component — runs on every dashboard render. The query is
 * inherently small (tens of rows at most, bounded by RLS + 60-min
 * window) so we don't bother with caching.
 */

import Link from 'next/link';
import { getHotLeadsNow } from '@/lib/data/engagement';
import { BentoCard } from '@/components/ui/bento-card';
import { EngagementScoreChip } from '@/components/ui/engagement-score-chip';

function formatMinutesAgo(iso: string): string {
  const delta = Math.max(0, Date.now() - new Date(iso).getTime());
  const mins = Math.floor(delta / 60_000);
  if (mins < 1) return 'or ora';
  if (mins < 60) return `${mins} min fa`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h fa`;
}

export async function HotLeadsNow({
  minutes = 60,
  limit = 5,
}: {
  minutes?: number;
  limit?: number;
}) {
  const rows = await getHotLeadsNow({ minutes, limit });

  return (
    <BentoCard>
      <header className="mb-2">
        <h3 className="text-base font-semibold">Caldi adesso</h3>
        <p className="text-xs text-on-surface-variant">
          Lead più attivi sul portale negli ultimi {minutes} minuti
        </p>
      </header>
      {rows.length === 0 ? (
        <p className="mt-2 text-sm text-on-surface-variant">
          Nessuna attività sul portale nell&apos;ultima ora. Torna più tardi —
          o invia qualche outreach per riscaldare la pipeline.
        </p>
      ) : (
        <ul className="mt-2 divide-y divide-outline-variant">
          {rows.map((r) => (
            <li
              key={r.lead_id}
              className="flex items-center justify-between gap-3 py-2"
            >
              <div className="min-w-0">
                <Link
                  href={`/leads/${r.lead_id}`}
                  className="truncate font-medium hover:underline"
                >
                  {r.display_name ?? r.public_slug ?? r.lead_id.slice(0, 8)}
                </Link>
                <p className="text-xs text-on-surface-variant">
                  {r.recent_events} event
                  {r.recent_events === 1 ? 'o' : 'i'} ·{' '}
                  {formatMinutesAgo(r.last_event_at)}
                </p>
              </div>
              <EngagementScoreChip
                score={r.engagement_score}
                // HotLeadsNow only shows leads with a portal event in
                // the window, so a score of 0 means "not yet rolled";
                // passing null keeps the chip honest.
                updatedAt={r.engagement_score > 0 ? 'now' : null}
              />
            </li>
          ))}
        </ul>
      )}
    </BentoCard>
  );
}
