/**
 * Follow-up state derivation — pure, no JSX, no I/O.
 *
 * Mirrors the decision tree in
 * `apps/api/src/services/followup_scenario_service.py`. The Python
 * service is the source of truth; this file replicates the same
 * thresholds + cooldowns so the dashboard can answer
 *
 *   "what is the system going to do with this lead, and when?"
 *
 * without a per-row API roundtrip. Keep the constants in mirror with
 * `SCORE_HOT_MIN` etc. — when the Python file changes, change here too.
 */

import type { LeadListRow, LeadStatus } from '@/types/db';

// ---------------------------------------------------------------------------
// Score thresholds (mirror followup_scenario_service.py:49-52)
// ---------------------------------------------------------------------------
export const SCORE_LUKEWARM_MIN = 1;
export const SCORE_ENGAGED_MIN = 21;
export const SCORE_INTERESSATO_MIN = 41;
export const SCORE_HOT_MIN = 61;

// followup_scenario_service.py:60-67
export const COOLDOWN_DAYS: Record<FollowUpKind, number> = {
  manual: 30,
  interessato: 7,
  engaged: 10,
  lukewarm: 14,
  riattivazione: 30,
  // The following two entries are inert (no auto-email), but having
  // them in the map keeps the cooldown lookup total and TS-friendly.
  inattivo: 30,
  conversazione: 30,
};

export const RIATTIVAZIONE_PEAK_MIN = 40;
export const RIATTIVAZIONE_SILENT_DAYS = 14;

// Pipeline statuses where a human is already talking to the lead;
// the system intentionally stops touching them.
const CONVERSATION_STATUSES: ReadonlySet<LeadStatus> = new Set([
  'whatsapp',
  'appointment',
  'closed_won',
  'closed_lost',
]);

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export type FollowUpKind =
  | 'manual'
  | 'interessato'
  | 'engaged'
  | 'lukewarm'
  | 'riattivazione'
  | 'inattivo'
  | 'conversazione';

export interface FollowUpState {
  kind: FollowUpKind;
  /** Short label for the chip ("Ricontatta · Caldo", "Auto · Engaged", …). */
  label: string;
  /** Single-paragraph tooltip explaining what the system is doing. */
  tooltip: string;
  /** When the next automated email is due. NULL when no email is scheduled
   *  (manual handoff, in-conversation, or never-yet-touched lead). */
  nextEmailAt: Date | null;
  /** Sort weight — lower = more urgent. Used by the table sort key. */
  weight: number;
}

type FollowUpInputs = Pick<
  LeadListRow,
  | 'engagement_score'
  | 'engagement_peak_score'
  | 'last_followup_scenario'
  | 'last_followup_sent_at'
  | 'hot_lead_alerted_at'
  | 'pipeline_status'
  | 'last_portal_event_at'
>;

const DAY_MS = 24 * 60 * 60 * 1000;

export function followUpState(
  row: FollowUpInputs,
  now: Date = new Date(),
): FollowUpState {
  // 1. Already in conversation — system never emails these.
  if (
    row.pipeline_status &&
    CONVERSATION_STATUSES.has(row.pipeline_status)
  ) {
    return {
      kind: 'conversazione',
      label: 'In conversazione',
      tooltip:
        'Il lead è già in trattativa diretta (WhatsApp, appuntamento, chiusura). Il sistema non invia più email automatiche.',
      nextEmailAt: null,
      weight: -1,
    };
  }

  const score = row.engagement_score ?? 0;

  // 2. Hot — score >= 61 → manual handoff.
  if (score >= SCORE_HOT_MIN) {
    const alerted = row.hot_lead_alerted_at
      ? new Date(row.hot_lead_alerted_at)
      : null;
    const alertedRel = alerted ? relativeShort(now, alerted) : 'da poco';
    return {
      kind: 'manual',
      label: 'Ricontatta · Caldo',
      tooltip:
        `Lead cottissimo (engagement ${score}/100). Il sistema ha sospeso ` +
        `le email automatiche e ti chiede di chiamare tu. Notifica inviata ${alertedRel}.`,
      nextEmailAt: null,
      weight: 0,
    };
  }

  // 3. Tiered automatic scenarios.
  if (score >= SCORE_INTERESSATO_MIN) {
    return autoScenario(row, 'interessato', score, now);
  }
  if (score >= SCORE_ENGAGED_MIN) {
    return autoScenario(row, 'engaged', score, now);
  }
  if (score >= SCORE_LUKEWARM_MIN) {
    return autoScenario(row, 'lukewarm', score, now);
  }

  // 4. Riattivazione — was warm in the past, now silent.
  const peak = row.engagement_peak_score ?? 0;
  const lastEvent = row.last_portal_event_at
    ? new Date(row.last_portal_event_at)
    : null;
  if (
    score === 0 &&
    peak >= RIATTIVAZIONE_PEAK_MIN &&
    lastEvent !== null &&
    now.getTime() - lastEvent.getTime() >= RIATTIVAZIONE_SILENT_DAYS * DAY_MS
  ) {
    const nextEmailAt = nextSendDate(row.last_followup_sent_at, 'riattivazione');
    return {
      kind: 'riattivazione',
      label: 'Auto · Riattivazione',
      tooltip:
        `Era caldo in passato (peak ${peak}/100), ora silenzioso da oltre ` +
        `${RIATTIVAZIONE_SILENT_DAYS} giorni. Il sistema invia un'email di ` +
        `riattivazione una tantum.${formatNextEmail(nextEmailAt, now)}`,
      nextEmailAt,
      weight: 4,
    };
  }

  // 5. Otherwise: nothing to do.
  return {
    kind: 'inattivo',
    label: 'Inattivo',
    tooltip:
      'Nessuna attività recente. Il sistema attende un nuovo segnale dal lead.',
    nextEmailAt: null,
    weight: 5,
  };
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function autoScenario(
  row: FollowUpInputs,
  kind: 'lukewarm' | 'engaged' | 'interessato',
  score: number,
  now: Date,
): FollowUpState {
  const cooldown = COOLDOWN_DAYS[kind];
  const nextEmailAt = nextSendDate(row.last_followup_sent_at, kind);
  const labels: Record<typeof kind, string> = {
    interessato: 'Auto · Interessato',
    engaged: 'Auto · Engaged',
    lukewarm: 'Auto · Lukewarm',
  };
  const weights: Record<typeof kind, number> = {
    interessato: 1,
    engaged: 2,
    lukewarm: 3,
  };
  return {
    kind,
    label: labels[kind],
    tooltip:
      `Engagement ${score}/100. Il sistema invia automaticamente un'email ` +
      `di scenario "${kind}" ogni ${cooldown} giorni.${formatNextEmail(nextEmailAt, now)}`,
    nextEmailAt,
    weight: weights[kind],
  };
}

function nextSendDate(
  lastSentAt: string | null,
  kind: FollowUpKind,
): Date | null {
  if (!lastSentAt) return null;
  const cooldownMs = COOLDOWN_DAYS[kind] * DAY_MS;
  return new Date(new Date(lastSentAt).getTime() + cooldownMs);
}

function formatNextEmail(nextAt: Date | null, now: Date): string {
  if (!nextAt) return ' La prima email partirà al prossimo ciclo (entro 24h).';
  const diffMs = nextAt.getTime() - now.getTime();
  if (diffMs <= 0) return ' Prossima email: in arrivo nel prossimo ciclo notturno.';
  const days = Math.ceil(diffMs / DAY_MS);
  if (days === 1) return ' Prossima email: domani.';
  return ` Prossima email: tra ${days} giorni.`;
}

function relativeShort(now: Date, then: Date): string {
  const diffMs = now.getTime() - then.getTime();
  if (diffMs < 60_000) return 'or ora';
  if (diffMs < 3600_000) {
    const m = Math.round(diffMs / 60_000);
    return `${m} min fa`;
  }
  if (diffMs < DAY_MS) {
    const h = Math.round(diffMs / 3600_000);
    return `${h}h fa`;
  }
  const d = Math.round(diffMs / DAY_MS);
  return `${d}gg fa`;
}
