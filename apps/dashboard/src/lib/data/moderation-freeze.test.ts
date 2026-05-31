import { describe, expect, it } from 'vitest';

import type { LeadDetailRow } from '@/types/db';
import {
  freezeModeratedLead,
  freezePipelineStatus,
  isNeutralEventType,
} from './moderation-freeze';

describe('isNeutralEventType', () => {
  it('keeps operator-side + system + delivery events', () => {
    for (const t of [
      'lead.outreach_sent',
      'lead.email_delivered',
      'lead.rendered',
      'lead.scored',
      'lead.postal_shipped',
      'lead.followup_sent_step1',
      'lead.followup_sent_step3',
    ]) {
      expect(isNeutralEventType(t)).toBe(true);
    }
  });

  it('hides every prospect reaction (allow-list: unknown ⇒ hidden)', () => {
    for (const t of [
      'lead.email_opened',
      'lead.email_clicked',
      'lead.portal_visited',
      'lead.whatsapp_click',
      'lead.appointment_requested',
      'lead.bolletta_uploaded',
      'lead.optout_requested',
      'lead.some_future_reaction',
    ]) {
      expect(isNeutralEventType(t)).toBe(false);
    }
  });
});

describe('freezePipelineStatus', () => {
  it('rolls reaction statuses back to sent when outreach was sent', () => {
    for (const s of ['opened', 'clicked', 'engaged', 'whatsapp', 'appointment', 'closed_won'] as const) {
      expect(freezePipelineStatus(s, '2026-05-30T10:00:00Z')).toBe('sent');
    }
  });

  it('rolls reaction statuses back to new when nothing was sent', () => {
    expect(freezePipelineStatus('clicked', null)).toBe('new');
  });

  it('leaves pre-reaction + compliance statuses untouched', () => {
    expect(freezePipelineStatus('sent', '2026-05-30T10:00:00Z')).toBe('sent');
    expect(freezePipelineStatus('delivered', '2026-05-30T10:00:00Z')).toBe('delivered');
    expect(freezePipelineStatus('new', null)).toBe('new');
    expect(freezePipelineStatus('blacklisted', '2026-05-30T10:00:00Z')).toBe('blacklisted');
  });
});

describe('freezeModeratedLead', () => {
  const base = {
    id: 'l1',
    pipeline_status: 'engaged',
    score: 72,
    score_tier: 'hot',
    outreach_sent_at: '2026-05-29T08:00:00Z',
    outreach_delivered_at: '2026-05-29T08:01:00Z',
    outreach_opened_at: '2026-05-29T09:00:00Z',
    outreach_clicked_at: '2026-05-29T09:05:00Z',
    outreach_replied_at: '2026-05-29T09:10:00Z',
    whatsapp_initiated_at: '2026-05-29T09:20:00Z',
    dashboard_visited_at: '2026-05-29T09:30:00Z',
    engagement_score: 93,
    engagement_peak_score: 93,
    engagement_score_updated_at: '2026-05-30T21:30:00Z',
    last_portal_event_at: '2026-05-30T21:30:00Z',
    portal_sessions: 4,
    portal_total_time_sec: 220,
    deepest_scroll_pct: 90,
    hot_lead_alerted_at: '2026-05-30T21:31:00Z',
    last_followup_scenario: 'engaged',
    last_followup_sent_at: '2026-05-30T07:00:00Z',
    feedback: 'appointment_set',
    feedback_at: '2026-05-30T22:00:00Z',
    feedback_notes: 'richiamare',
    operator_released_at: null,
  } as unknown as LeadDetailRow;

  it('zeroes every reaction signal but preserves identity + sent facts', () => {
    const f = freezeModeratedLead(base);
    // reactions withheld
    expect(f.engagement_score).toBe(0);
    expect(f.engagement_peak_score).toBe(0);
    expect(f.engagement_score_updated_at).toBeNull();
    expect(f.pipeline_status).toBe('sent');
    expect(f.outreach_opened_at).toBeNull();
    expect(f.outreach_clicked_at).toBeNull();
    expect(f.outreach_replied_at).toBeNull();
    expect(f.whatsapp_initiated_at).toBeNull();
    expect(f.dashboard_visited_at).toBeNull();
    expect(f.last_portal_event_at).toBeNull();
    expect(f.portal_sessions).toBe(0);
    expect(f.portal_total_time_sec).toBe(0);
    expect(f.deepest_scroll_pct).toBe(0);
    expect(f.hot_lead_alerted_at).toBeNull();
    expect(f.last_followup_scenario).toBeNull();
    expect(f.last_followup_sent_at).toBeNull();
    expect(f.feedback).toBeNull();
    expect(f.feedback_at).toBeNull();
    expect(f.feedback_notes).toBeNull();
    // sent-time facts + identity preserved
    expect(f.id).toBe('l1');
    expect(f.score).toBe(72);
    expect(f.outreach_sent_at).toBe('2026-05-29T08:00:00Z');
    expect(f.outreach_delivered_at).toBe('2026-05-29T08:01:00Z');
  });

  it('does not mutate the input', () => {
    const snapshot = JSON.parse(JSON.stringify(base));
    freezeModeratedLead(base);
    expect(JSON.parse(JSON.stringify(base))).toEqual(snapshot);
  });
});
