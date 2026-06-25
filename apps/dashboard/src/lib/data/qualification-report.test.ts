import { describe, expect, it } from 'vitest';

import { shapeQualificationReport } from './qualification-report';

const row = (
  cohort: string,
  sent: number,
  visited: number,
  appointments = 0,
  engaged = 0,
  visit_rate: number | null = null,
) => ({ cohort, sent, visited, appointments, engaged, visit_rate });

describe('shapeQualificationReport', () => {
  it('splits the two cohorts and computes the lift', () => {
    const r = shapeQualificationReport([
      row('qualified', 156, 13, 1, 14, 8.3),
      row('legacy', 326, 14, 1, 14, 4.3),
    ]);
    expect(r.qualified.sent).toBe(156);
    expect(r.qualified.visitRate).toBe(8.3);
    expect(r.legacy.sent).toBe(326);
    expect(r.legacy.visitRate).toBe(4.3);
    // 8.3 / 4.3 ≈ 1.9× — the qualification lift.
    expect(r.lift).toBe(1.9);
  });

  it('defaults a missing cohort to zeros (no crash on a fresh tenant)', () => {
    const r = shapeQualificationReport([row('qualified', 10, 2, 0, 2, 20)]);
    expect(r.legacy.sent).toBe(0);
    expect(r.legacy.visitRate).toBe(0);
    // No legacy baseline → lift is null, not Infinity/NaN.
    expect(r.lift).toBeNull();
  });

  it('coerces bigint-as-string counts from the RPC', () => {
    const r = shapeQualificationReport([
      // PostgREST returns bigint as string.
      { cohort: 'qualified', sent: '5', visited: '1', appointments: '0', engaged: '1', visit_rate: '20.0' } as never,
      row('legacy', 5, 0, 0, 0, 0),
    ]);
    expect(r.qualified.sent).toBe(5);
    expect(r.qualified.visitRate).toBe(20);
    expect(r.lift).toBeNull(); // legacy rate 0
  });
});
