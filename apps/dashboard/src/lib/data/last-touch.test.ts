import { describe, expect, it } from 'vitest';

import { latestTouchAt, type LeadTouchTimestamps } from './last-touch';

/** All-null touch record; spread an override on top per test. */
const EMPTY: LeadTouchTimestamps = {
  outreach_sent_at: null,
  last_followup_sent_at: null,
  outreach_delivered_at: null,
  outreach_opened_at: null,
  outreach_clicked_at: null,
  outreach_replied_at: null,
  dashboard_visited_at: null,
  last_portal_event_at: null,
  whatsapp_initiated_at: null,
  appointment_requested_at: null,
};

describe('latestTouchAt', () => {
  it('returns null when the lead has no interaction yet', () => {
    expect(latestTouchAt(EMPTY)).toBeNull();
  });

  it('returns the single present touch', () => {
    expect(
      latestTouchAt({ ...EMPTY, outreach_sent_at: '2026-06-19T07:10:00Z' }),
    ).toBe('2026-06-19T07:10:00Z');
  });

  it('takes the MAX by recency, not the old fixed priority — the Decò case', () => {
    // Sent + first portal visit on 06-19; follow-up + fresh portal visit on
    // 06-22. The old priority fallback returned the frozen dashboard_visited_at
    // (06-19) → "3 giorni fa". The fix must return the 06-22 events.
    const lead: LeadTouchTimestamps = {
      ...EMPTY,
      outreach_sent_at: '2026-06-19T07:10:00Z',
      outreach_opened_at: '2026-06-19T07:11:00Z',
      outreach_clicked_at: '2026-06-19T07:11:00Z',
      dashboard_visited_at: '2026-06-19T07:11:00Z', // frozen first visit
      last_followup_sent_at: '2026-06-22T06:58:00Z', // newest: our follow-up
      last_portal_event_at: '2026-06-22T06:58:00Z', // newest: prospect re-visit
    };
    expect(latestTouchAt(lead)).toBe('2026-06-22T06:58:00Z');
  });

  it('does not let a higher-priority-but-older field win', () => {
    // dashboard_visited_at was the old top priority; outreach_sent_at is newer.
    const lead: LeadTouchTimestamps = {
      ...EMPTY,
      dashboard_visited_at: '2026-06-10T09:00:00Z',
      outreach_sent_at: '2026-06-14T09:00:00Z',
    };
    expect(latestTouchAt(lead)).toBe('2026-06-14T09:00:00Z');
  });

  it('counts an appointment request — the hottest inbound signal', () => {
    const lead: LeadTouchTimestamps = {
      ...EMPTY,
      outreach_sent_at: '2026-06-19T07:10:00Z',
      appointment_requested_at: '2026-06-21T15:30:00Z',
    };
    expect(latestTouchAt(lead)).toBe('2026-06-21T15:30:00Z');
  });

  it('compares by epoch, not lexicographically (mixed fractional precision)', () => {
    // '...:01Z' is later than '...:00.500Z' but sorts BEFORE it as a string
    // ('1' < '0.5' lexicographically would be wrong). Epoch compare gets it right.
    const lead: LeadTouchTimestamps = {
      ...EMPTY,
      outreach_opened_at: '2026-06-20T10:00:00.500Z',
      last_portal_event_at: '2026-06-20T10:00:01Z',
    };
    expect(latestTouchAt(lead)).toBe('2026-06-20T10:00:01Z');
  });
});
