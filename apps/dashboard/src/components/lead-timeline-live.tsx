'use client';

/**
 * Live timeline for a single lead detail page.
 *
 * The raw `events` table mixes pipeline-internal traffic (lead.identified,
 * lead.scored, roof.scanned, internal retries) with operator-relevant
 * activity (outreach sent, email opened, portal visit, bolletta uploaded,
 * appointment requested). Showing all of it produces a confusing wall of
 * technical entries — we render only the operator-relevant ones by
 * default, and offer a toggle to expand the technical history.
 *
 * Pattern mirror of `realtime-toaster.tsx` (global tenant feed):
 *   - Seeded from SSR via `initialEvents` so the first render is
 *     identical to the old static timeline (no flash, no skeleton).
 *   - Subscribes to Supabase Realtime on `postgres_changes` for
 *     INSERTs into `events` filtered by `lead_id=eq.{leadId}`.
 *   - De-duplicates by `id` and prepends — events are append-only.
 */

import { useEffect, useMemo, useRef, useState } from 'react';

import { createBrowserClient } from '@/lib/supabase/client';
import { relativeTime } from '@/lib/utils';
import type { EventRow } from '@/types/db';

interface LeadTimelineLiveProps {
  leadId: string;
  initialEvents: EventRow[];
}

/** Plain-Italian labels for known event types. */
const EVENT_LABEL: Record<string, string> = {
  // Pipeline (hidden by default — toggle to show)
  'roof.scanned': 'Tetto scansionato',
  'lead.identified': 'Soggetto identificato',
  'lead.scored': 'Score calcolato',
  'lead.rendered': 'Rendering generato',
  'lead.render_skipped': 'Rendering saltato',
  'lead.contacted': 'Contattato',

  // Outreach (operator-relevant)
  'lead.outreach_sent': 'Email inviata',
  'lead.outreach_skipped_tier': 'Invio bloccato (piano insufficiente)',
  'lead.outreach_ratelimited': 'Invio rinviato (reputazione dominio)',
  'lead.followup_sent_step2': 'Follow-up #2 inviato',
  'lead.followup_sent_step3': 'Follow-up #3 inviato',

  // Engagement (operator-relevant — what the lead is doing)
  'lead.email_delivered': 'Email consegnata',
  'lead.email_opened': 'Email aperta',
  'lead.email_clicked': 'Cliccato sul link',
  'lead.email_bounced': 'Email rimbalzata',
  'lead.email_complained': 'Segnalata come spam',
  'lead.portal_visited': 'Ha aperto il portale',
  'lead.bolletta_uploaded': 'Bolletta caricata',
  'lead.whatsapp_click': 'Cliccato su WhatsApp',

  // Conversion
  'lead.appointment_requested': 'Appuntamento richiesto',
  'lead.optout_requested': 'Disiscrizione',
};

type Category = 'outreach' | 'engagement' | 'conversion' | 'pipeline' | 'default';

function classify(eventType: string): Category {
  if (eventType === 'lead.appointment_requested') return 'conversion';
  if (
    eventType === 'lead.email_opened' ||
    eventType === 'lead.email_clicked' ||
    eventType === 'lead.portal_visited' ||
    eventType === 'lead.bolletta_uploaded' ||
    eventType === 'lead.whatsapp_click'
  ) {
    return 'engagement';
  }
  if (eventType.startsWith('lead.outreach') || eventType.startsWith('lead.followup')) {
    return 'outreach';
  }
  return 'pipeline';
}

/** Events the operator wants to see. Everything else is pipeline noise
 * available behind the "tecnici" toggle. */
const OPERATOR_RELEVANT: ReadonlySet<Category> = new Set([
  'outreach',
  'engagement',
  'conversion',
]);

const DOT: Record<Category, string> = {
  outreach: 'bg-sky-500',
  engagement: 'bg-indigo-500',
  conversion: 'bg-emerald-500',
  pipeline: 'bg-on-surface-variant/40',
  default: 'bg-on-surface-variant',
};

/** Normalise the id — Postgres bigint serializes as number via REST, string via Realtime. */
function eventKey(e: EventRow): string {
  return String(e.id);
}

interface GroupedEvent {
  type: string;
  category: Category;
  count: number;
  firstAt: string | null;
  lastAt: string | null;
  ids: string[];
}

/** Collapse consecutive same-type events into a single row with a count.
 * "Email aperta · 3 volte · ultima 2 min fa" reads better than three
 * separate "Email aperta" rows stacked together. */
function groupConsecutive(sorted: EventRow[]): GroupedEvent[] {
  const out: GroupedEvent[] = [];
  for (const e of sorted) {
    const last = out[out.length - 1];
    if (last && last.type === e.event_type) {
      last.count += 1;
      // sorted descending → first encountered is the most recent
      last.firstAt = e.occurred_at ?? last.firstAt;
      last.ids.push(eventKey(e));
    } else {
      out.push({
        type: e.event_type,
        category: classify(e.event_type),
        count: 1,
        firstAt: e.occurred_at,
        lastAt: e.occurred_at,
        ids: [eventKey(e)],
      });
    }
  }
  return out;
}

export function LeadTimelineLive({ leadId, initialEvents }: LeadTimelineLiveProps) {
  const [events, setEvents] = useState<EventRow[]>(initialEvents);
  const [showTechnical, setShowTechnical] = useState(false);
  const seededFor = useRef(leadId);
  if (seededFor.current !== leadId) {
    seededFor.current = leadId;
    setEvents(initialEvents);
  }

  useEffect(() => {
    const supabase = createBrowserClient();
    const channel = supabase
      .channel(`events:lead:${leadId}`)
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: 'events',
          filter: `lead_id=eq.${leadId}`,
        },
        (msg) => {
          const row = msg.new as EventRow;
          setEvents((prev) => {
            const key = eventKey(row);
            if (prev.some((e) => eventKey(e) === key)) return prev;
            return [row, ...prev];
          });
        },
      )
      .subscribe();
    return () => {
      supabase.removeChannel(channel);
    };
  }, [leadId]);

  const sorted = useMemo(
    () =>
      [...events].sort((a, b) =>
        (b.occurred_at ?? '').localeCompare(a.occurred_at ?? ''),
      ),
    [events],
  );

  const filtered = useMemo(() => {
    if (showTechnical) return sorted;
    return sorted.filter((e) => OPERATOR_RELEVANT.has(classify(e.event_type)));
  }, [sorted, showTechnical]);

  const grouped = useMemo(() => groupConsecutive(filtered), [filtered]);
  const technicalCount = sorted.length - filtered.length;

  if (sorted.length === 0) {
    return (
      <p className="rounded-lg bg-surface-container-low p-6 text-sm text-on-surface-variant">
        Nessun evento ancora registrato per questo lead.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {grouped.length === 0 ? (
        <p className="rounded-lg bg-surface-container-low p-6 text-sm text-on-surface-variant">
          Nessuna interazione del lead ancora.{' '}
          {technicalCount > 0 && (
            <button
              type="button"
              onClick={() => setShowTechnical(true)}
              className="font-semibold text-primary hover:underline"
            >
              Mostra {technicalCount} eventi tecnici
            </button>
          )}
        </p>
      ) : (
        <ol className="space-y-1">
          {grouped.map((g) => (
            <li
              key={g.ids[0]}
              className="flex items-start gap-4 rounded-lg px-3 py-2 text-sm transition-colors hover:bg-surface-container-low"
            >
              <span className="w-28 shrink-0 text-xs text-on-surface-variant">
                {relativeTime(g.firstAt)}
              </span>
              <span className="mt-1.5 flex items-center">
                <span
                  className={`inline-block h-2 w-2 rounded-full ${DOT[g.category]}`}
                  aria-hidden="true"
                />
              </span>
              <div className="flex-1">
                <p className="font-semibold">
                  {EVENT_LABEL[g.type] ?? g.type}
                  {g.count > 1 && (
                    <span className="ml-2 rounded-full bg-surface-container-high px-1.5 py-0.5 text-[10px] font-bold tabular-nums text-on-surface-variant">
                      ×{g.count}
                    </span>
                  )}
                </p>
                {g.count > 1 && g.lastAt && g.firstAt !== g.lastAt && (
                  <p className="text-xs text-on-surface-variant">
                    Prima volta {relativeTime(g.lastAt)}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}

      {technicalCount > 0 && (
        <button
          type="button"
          onClick={() => setShowTechnical((v) => !v)}
          className="text-xs font-semibold uppercase tracking-widest text-on-surface-variant hover:text-on-surface"
        >
          {showTechnical
            ? '◉ Nascondi eventi tecnici'
            : `○ Mostra ${technicalCount} eventi tecnici (rendering, scoring, ecc.)`}
        </button>
      )}
    </div>
  );
}
