/**
 * Pure-function test for the realtime toaster event classifier.
 * Exercises every branch so silent drift (e.g. a new postal event
 * type rendered as raw "lead.postal_*" text) gets caught in CI.
 */
import { describe, it, expect } from 'vitest';

import { classify } from './realtime-toaster';

describe('classify', () => {
  it('outreach sent', () => {
    const out = classify('lead.outreach_sent', null);
    expect(out.title).toBe('Outreach inviata');
    expect(out.type).toBe('outreach');
  });

  it('follow-up step embeds the step number', () => {
    expect(classify('lead.followup_sent_2', null).title).toContain('2');
    expect(classify('lead.followup_sent_3', null).type).toBe('outreach');
  });

  it('portal visited is engagement', () => {
    const out = classify('lead.portal_visited', null);
    expect(out.type).toBe('engagement');
    expect(out.title).toMatch(/portal/i);
  });

  it('appointment surfaces contact_name when available', () => {
    const out = classify('lead.appointment_requested', {
      contact_name: 'Mario Rossi',
    });
    expect(out.type).toBe('conversion');
    expect(out.subtitle).toBe('Mario Rossi');
  });

  it('appointment without name falls back to event_type', () => {
    const out = classify('lead.appointment_requested', null);
    expect(out.type).toBe('conversion');
    expect(out.subtitle).toBe('lead.appointment_requested');
  });

  it('opt-out', () => {
    expect(classify('lead.optout_requested', null).title).toMatch(/opt/i);
  });

  // ------------------------------------------------------------------
  // Pixart postal — must match the TrackingAgent's emitted event_types.
  // ------------------------------------------------------------------
  it('postal printed', () => {
    expect(classify('lead.postal_printed', null).title).toBe('Cartolina stampata');
  });
  it('postal shipped', () => {
    expect(classify('lead.postal_shipped', null).title).toBe('Cartolina spedita');
  });
  it('postal delivered', () => {
    const out = classify('lead.postal_delivered', null);
    expect(out.title).toBe('Cartolina consegnata');
    expect(out.type).toBe('outreach');
  });
  it('postal returned is "default" styling (not conversion)', () => {
    const out = classify('lead.postal_returned', null);
    expect(out.title).toMatch(/tornata/i);
    expect(out.type).toBe('default');
  });

  it('unknown event falls back to raw event_type', () => {
    const out = classify('lead.some_new_thing', null);
    expect(out.title).toBe('lead.some_new_thing');
    expect(out.type).toBe('default');
  });
});
