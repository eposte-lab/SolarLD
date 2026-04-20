'use client';

/**
 * Live timeline for a single lead detail page.
 *
 * Pattern mirror of `realtime-toaster.tsx` (global tenant feed):
 *   - Seeded from SSR via `initialEvents` so the first render is
 *     identical to the old static timeline (no flash, no skeleton).
 *   - Subscribes to Supabase Realtime on `postgres_changes` for
 *     INSERTs into `events` filtered by `lead_id=eq.{leadId}` so the
 *     client receives only the events for this lead (RLS still
 *     enforces tenant scoping on top).
 *   - De-duplicates by `id` and prepends — events are append-only so
 *     we never need to handle UPDATE/DELETE.
 *
 * The `events` table is monthly-partitioned. Migration 0019 ensures
 * every partition (current + future) is registered with the
 * `supabase_realtime` publication — without that, INSERTs on the
 * new month's partition silently don't broadcast.
 */

import { useEffect, useMemo, useRef, useState } from 'react';

import { createBrowserClient } from '@/lib/supabase/client';
import { relativeTime } from '@/lib/utils';
import type { EventRow } from '@/types/db';

interface LeadTimelineLiveProps {
  leadId: string;
  initialEvents: EventRow[];
}

/** Pretty labels for known event types — falls back to the raw key. */
const EVENT_LABEL: Record<string, string> = {
  'roof.scanned': 'Tetto scansionato',
  'lead.identified': 'Soggetto identificato',
  'lead.scored': 'Scoring calcolato',
  'lead.rendered': 'Rendering generato',
  'lead.render_skipped': 'Rendering saltato',
  'lead.outreach_sent': 'Outreach inviata',
  'lead.outreach_skipped_tier': 'Outreach bloccata (piano insufficiente)',
  'lead.outreach_ratelimited': 'Outreach rinviata (reputazione dominio)',
  'lead.followup_sent_step2': 'Follow-up step 2 inviato',
  'lead.followup_sent_step3': 'Follow-up step 3 inviato',
  'lead.email_delivered': 'Email consegnata',
  'lead.email_opened': 'Email aperta',
  'lead.email_clicked': 'Click sul link',
  'lead.email_bounced': 'Email bounced',
  'lead.email_complained': 'Segnalato come spam',
  'lead.portal_visited': 'Ha aperto il portale',
  'lead.whatsapp_click': 'Click su WhatsApp',
  'lead.appointment_requested': 'Appuntamento richiesto',
  'lead.optout_requested': 'Opt-out',
};

/** Accent + pastille colour per event category. */
function classify(eventType: string): 'outreach' | 'engagement' | 'conversion' | 'pipeline' | 'default' {
  if (eventType === 'lead.appointment_requested') return 'conversion';
  if (
    eventType === 'lead.email_opened' ||
    eventType === 'lead.email_clicked' ||
    eventType === 'lead.portal_visited' ||
    eventType === 'lead.whatsapp_click'
  )
    return 'engagement';
  if (eventType.startsWith('lead.outreach') || eventType.startsWith('lead.followup'))
    return 'outreach';
  if (eventType === 'roof.scanned' || eventType === 'lead.scored' || eventType === 'lead.rendered' || eventType === 'lead.identified')
    return 'pipeline';
  return 'default';
}

const DOT: Record<ReturnType<typeof classify>, string> = {
  outreach: 'bg-sky-500',
  engagement: 'bg-indigo-500',
  conversion: 'bg-emerald-500',
  pipeline: 'bg-primary',
  default: 'bg-on-surface-variant',
};

/** Normalise the id — Postgres bigint serializes as number via REST, string via Realtime. */
function eventKey(e: EventRow): string {
  return String(e.id);
}

export function LeadTimelineLive({ leadId, initialEvents }: LeadTimelineLiveProps) {
  const [events, setEvents] = useState<EventRow[]>(initialEvents);
  // Reset when the lead changes (shouldn't happen in this component lifetime
  // because the parent page is keyed on id, but defensive for HMR).
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
            // Dedup: if SSR already included this id, skip.
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

  if (sorted.length === 0) {
    return (
      <p className="rounded-lg bg-surface-container-low p-6 text-sm text-on-surface-variant">
        Nessun evento ancora registrato per questo lead.
      </p>
    );
  }

  return (
    <ol className="space-y-1">
      {sorted.map((e) => {
        const category = classify(e.event_type);
        return (
          <li
            key={eventKey(e)}
            className="flex items-start gap-4 rounded-lg px-3 py-2 text-sm transition-colors hover:bg-surface-container-low"
          >
            <span className="w-28 shrink-0 text-xs text-on-surface-variant">
              {relativeTime(e.occurred_at)}
            </span>
            <span className="mt-1.5 flex items-center">
              <span
                className={`inline-block h-2 w-2 rounded-full ${DOT[category]}`}
                aria-hidden="true"
              />
            </span>
            <div className="flex-1">
              <p className="font-semibold">
                {EVENT_LABEL[e.event_type] ?? e.event_type}
              </p>
              {e.event_source && (
                <p className="text-xs text-on-surface-variant">
                  via {e.event_source}
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
