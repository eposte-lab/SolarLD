import { describe, expect, it } from 'vitest';

import { aggregateCampaignResults } from './campaign-results';

function send(
  status: string,
  variant: string | null,
  lead: {
    id: string;
    province?: string | null;
    score_tier?: string | null;
    opened?: boolean;
    clicked?: boolean;
    replied?: boolean;
  },
) {
  return {
    status,
    experiment_variant: variant,
    leads: {
      id: lead.id,
      province: lead.province ?? 'NA',
      score_tier: lead.score_tier ?? 'hot',
      outreach_opened_at: lead.opened ? '2026-06-20T10:00:00Z' : null,
      outreach_clicked_at: lead.clicked ? '2026-06-20T10:05:00Z' : null,
      outreach_replied_at: lead.replied ? '2026-06-20T11:00:00Z' : null,
    },
  };
}

describe('aggregateCampaignResults', () => {
  it('excludes failed sends from the sent count (the open-rate denominator)', () => {
    const rows = [
      send('sent', 'a', { id: 'l1', opened: true }),
      send('sent', 'a', { id: 'l2' }),
      send('failed', 'a', { id: 'l3', opened: true }), // must NOT count anywhere
    ];

    const row = aggregateCampaignResults(rows)[0]!;

    expect(row.sent).toBe(2); // not 3
    expect(row.opened).toBe(1); // l1 only — l3's open is on a failed send
    // open-rate the table renders = opened / sent = 1/2, never 1/3
    expect(row.opened / row.sent).toBe(0.5);
  });

  it('reads opens/clicks/replies from the LEAD, not the (sent/failed-only) send status', () => {
    const rows = [
      send('sent', 'a', { id: 'l1', opened: true, clicked: true }),
      send('sent', 'a', { id: 'l2', opened: true, replied: true }),
    ];

    const row = aggregateCampaignResults(rows)[0]!;

    expect(row.opened).toBe(2);
    expect(row.clicked).toBe(1);
    expect(row.replied).toBe(1);
  });

  it('counts a lead once even with multiple sends; sent stays send-level', () => {
    const rows = [
      send('sent', 'a', { id: 'l1', opened: true }),
      send('sent', 'a', { id: 'l1', opened: true }), // same lead, step 2
    ];

    const row = aggregateCampaignResults(rows)[0]!;

    expect(row.sent).toBe(2); // two emails went out
    expect(row.opened).toBe(1); // one distinct lead opened
  });

  it('delivered mirrors sent (no delivery webhook wired)', () => {
    const rows = [send('sent', null, { id: 'l1' })];
    const row = aggregateCampaignResults(rows)[0]!;
    expect(row.delivered).toBe(row.sent);
  });

  it('groups by (variant, province, tier) and drops all-failed groups', () => {
    const rows = [
      send('sent', 'a', { id: 'l1', province: 'NA' }),
      send('sent', 'b', { id: 'l2', province: 'NA' }),
      send('failed', 'b', { id: 'l3', province: 'SA' }), // only send in its group
    ];

    const out = aggregateCampaignResults(rows);

    expect(out).toHaveLength(2); // SA/b group vanishes — nothing was sent
    expect(out.every((r) => r.sent > 0)).toBe(true);
  });
});
