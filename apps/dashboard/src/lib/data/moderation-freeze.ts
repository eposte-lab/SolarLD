/**
 * Trial-moderation "freeze" helpers.
 *
 * For a moderated tenant (Total Trade), a contatto that has NOT yet been
 * promoted by the operator (`leads.operator_released_at IS NULL`) must be
 * shown in the dashboard exactly as it left the operator's hands: frozen
 * at its sent-time state. Every prospect REACTION — engagement score,
 * advanced pipeline status, email opens/clicks, portal activity, inbound
 * replies, bolletta upload, appointment request — is withheld until the
 * operator clicks "Promuovi a lead" in the super-admin queue.
 *
 * The /leads list + hot-leads widgets already hide un-promoted contatti
 * entirely (gate in lib/data/leads.ts). This module covers the one place
 * the tenant can still reach an un-promoted contatto: its scheda, opened
 * from /contatti. The freeze is applied server-side in
 * app/(dashboard)/leads/[id]/page.tsx so no client fetch can reveal a
 * live reaction; the global realtime toaster is muted separately
 * (components/realtime-toaster.tsx).
 *
 * `operator_released_at` keeps its column meaning ("operator promoted
 * this to a lead"); this only changes what the tenant sees before that.
 */

import type { LeadDetailRow, LeadStatus } from '@/types/db';

/**
 * Event types that are NOT a prospect reaction and stay visible on a
 * frozen contatto's timeline: operator-side sends + mail-server delivery
 * + system render/score. Everything else (email_opened, email_clicked,
 * portal_visited, whatsapp_click, appointment_requested,
 * bolletta_uploaded, optout_requested, …) is a reaction and is withheld.
 *
 * Allow-list (not deny-list) on purpose: a new reaction event type added
 * later defaults to hidden rather than leaking.
 */
const NEUTRAL_EVENT_TYPES: ReadonlySet<string> = new Set<string>([
  'lead.outreach_sent',
  'lead.email_delivered',
  'lead.rendered',
  'lead.scored',
  'lead.postal_printed',
  'lead.postal_shipped',
  'lead.postal_delivered',
  'lead.postal_returned',
]);

const NEUTRAL_EVENT_PREFIXES: readonly string[] = ['lead.followup_sent'];

/** True when an event is safe to show on a frozen (moderated,
 *  un-promoted) contatto's timeline — i.e. it is not a prospect reaction. */
export function isNeutralEventType(eventType: string): boolean {
  if (NEUTRAL_EVENT_TYPES.has(eventType)) return true;
  return NEUTRAL_EVENT_PREFIXES.some((p) => eventType.startsWith(p));
}

/**
 * Pipeline statuses that only exist because the prospect reacted. On a
 * frozen contatto we roll these back to the last operator-side state so
 * Total Trade sees the contatto exactly as sent. `delivered`/`sent`/
 * `new`/`ready_to_send` are pre-reaction and left untouched; `blacklisted`
 * is kept too (it is a compliance/suppression state, not a soft reaction,
 * and hiding it could let the tenant re-send to an opted-out contact).
 */
const REACTION_STATUSES: ReadonlySet<string> = new Set<string>([
  'opened',
  'clicked',
  'engaged',
  'to_call',
  'whatsapp',
  'appointment',
  'closed_won',
  'closed_lost',
]);

/** Returns the pipeline status a frozen contatto should display. */
export function freezePipelineStatus(
  status: LeadStatus,
  outreachSentAt: string | null,
): LeadStatus {
  if (!REACTION_STATUSES.has(status)) return status;
  return (outreachSentAt ? 'sent' : 'new') as LeadStatus;
}

/**
 * Returns a copy of the lead with every prospect-reaction signal zeroed —
 * the sent-time snapshot. Anagrafica, tetto, impianto, rendering, ICP
 * score and the sent-outreach facts (channel, sent/delivered timestamps)
 * are preserved; only reaction-derived fields are withheld.
 */
export function freezeModeratedLead<T extends LeadDetailRow>(lead: T): T {
  return {
    ...lead,
    pipeline_status: freezePipelineStatus(
      lead.pipeline_status,
      lead.outreach_sent_at,
    ),
    engagement_score: 0,
    engagement_peak_score: 0,
    engagement_score_updated_at: null,
    outreach_opened_at: null,
    outreach_clicked_at: null,
    outreach_replied_at: null,
    whatsapp_initiated_at: null,
    dashboard_visited_at: null,
    last_portal_event_at: null,
    portal_sessions: 0,
    portal_total_time_sec: 0,
    deepest_scroll_pct: 0,
    hot_lead_alerted_at: null,
    last_followup_scenario: null,
    last_followup_sent_at: null,
    feedback: null,
    feedback_notes: null,
    feedback_at: null,
  };
}
