/**
 * "Ultimo tocco" — the single most RECENT interaction on a lead.
 *
 * Why a max-over-timestamps and not a priority fallback: the original
 * helpers (leads-table `lastTouchOf`, temperature-board `lastEvent`) returned
 * the FIRST non-null of
 *
 *     dashboard_visited_at → outreach_opened_at → outreach_sent_at
 *
 * which was wrong on two counts:
 *
 *   1. It picked by a fixed PRIORITY, not by recency — so a follow-up we sent
 *      today, or a fresh portal visit today, stayed hidden behind an older
 *      higher-priority field.
 *   2. Its top priority, `dashboard_visited_at`, is FROZEN by the API to the
 *      prospect's FIRST portal visit (it is only written when still NULL —
 *      see `routes/public.py`). So it never reflects anything after that first
 *      hit, and the recurring `last_portal_event_at` (bumped on every visit)
 *      was ignored entirely.
 *
 * Real-world symptom (Decò Maxistore): outreach sent + first portal visit on
 * 06-19, then a follow-up sent AND a fresh portal visit on 06-22 — yet the row
 * read "3 giorni fa" because it returned the frozen 06-19 `dashboard_visited_at`.
 *
 * We now take the latest of every genuine touch timestamp — our outbound sends
 * (incl. the most recent follow-up) and the prospect's inbound engagement
 * (open, click, reply, recurring portal visit, WhatsApp, appointment request).
 * `created_at` is deliberately NOT a touch: lead creation isn't an interaction.
 */
import type { LeadListRow } from '@/types/db';

/** The subset of a lead's timestamps that count as a genuine interaction. */
export type LeadTouchTimestamps = Pick<
  LeadListRow,
  | 'outreach_sent_at'
  | 'last_followup_sent_at'
  | 'outreach_delivered_at'
  | 'outreach_opened_at'
  | 'outreach_clicked_at'
  | 'outreach_replied_at'
  | 'dashboard_visited_at'
  | 'last_portal_event_at'
  | 'whatsapp_initiated_at'
  | 'appointment_requested_at'
>;

/**
 * The most recent touch on the lead as an ISO timestamp, or `null` if the lead
 * has had no interaction yet (no send, no engagement). Callers that need a
 * non-null value (e.g. a "last activity" column) fall back to `created_at`.
 */
export function latestTouchAt(lead: LeadTouchTimestamps): string | null {
  const candidates: Array<string | null> = [
    lead.outreach_sent_at,
    lead.last_followup_sent_at,
    lead.outreach_delivered_at,
    lead.outreach_opened_at,
    lead.outreach_clicked_at,
    lead.outreach_replied_at,
    lead.dashboard_visited_at,
    lead.last_portal_event_at,
    lead.whatsapp_initiated_at,
    lead.appointment_requested_at,
  ];

  let latest: string | null = null;
  let latestMs = -Infinity;
  for (const ts of candidates) {
    if (!ts) continue;
    // Compare by epoch ms (robust to fractional-second precision differences)
    // rather than lexicographically on the ISO strings.
    const ms = new Date(ts).getTime();
    if (Number.isFinite(ms) && ms > latestMs) {
      latestMs = ms;
      latest = ts;
    }
  }
  return latest;
}
