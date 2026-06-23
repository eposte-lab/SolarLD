import { describe, expect, it } from 'vitest';

import { deriveStatusFlags, type OperationalStatus } from './pipeline-status';

const base: OperationalStatus = {
  renderDone: 100,
  renderStuck: 0,
  warehouseReady: 50,
  sendableNow: 50,
  picked: 2,
  sentToday: 10,
  dailyCap: 60,
  active: 18,
};

describe('deriveStatusFlags', () => {
  it('all healthy → single "ok" flag', () => {
    expect(deriveStatusFlags(base)).toEqual([{ tone: 'ok', text: 'Tutto regolare' }]);
  });

  it('stuck renders → danger flag naming the count', () => {
    const flags = deriveStatusFlags({ ...base, renderStuck: 42 });
    expect(flags[0]?.tone).toBe('danger');
    expect(flags[0]?.text).toContain('42');
  });

  it('empty warehouse → warn flag', () => {
    const flags = deriveStatusFlags({ ...base, warehouseReady: 0 });
    expect(flags.some((f) => f.tone === 'warn')).toBe(true);
  });

  it('daily cap reached → info flag with ratio', () => {
    const flags = deriveStatusFlags({ ...base, sentToday: 60, dailyCap: 60 });
    expect(flags.some((f) => f.tone === 'info' && f.text.includes('60/60'))).toBe(true);
  });

  it('no cap configured → never an "info" cap flag (no false "cap reached")', () => {
    const flags = deriveStatusFlags({ ...base, sentToday: 999, dailyCap: 0 });
    expect(flags.some((f) => f.tone === 'info')).toBe(false);
  });

  it('multiple problems stack (no "ok" when something is wrong)', () => {
    const flags = deriveStatusFlags({ ...base, renderStuck: 5, warehouseReady: 0 });
    expect(flags).toHaveLength(2);
    expect(flags.some((f) => f.text === 'Tutto regolare')).toBe(false);
  });
});
