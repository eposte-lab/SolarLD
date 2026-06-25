/**
 * Qualification report — two-cohort send comparison for the /invii section.
 *
 * "Qualified" sends = the contact went through the qualification pipeline
 * (NeverBounce validation + premium contact). "Legacy" = un-validated sends
 * (pre-system, or the days NeverBounce ran dry). Lets the owner SEE the lift the
 * qualification system delivers.
 *
 * Backed by the `qualification_kpi_report()` RPC (migration 0161), which
 * aggregates server-side under the caller's RLS — scoped to their tenant, and
 * not subject to PostgREST's 1000-row cap.
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';

export interface QualificationCohort {
  cohort: 'qualified' | 'legacy';
  sent: number;
  /** Dossier visits — the real engagement signal. */
  visited: number;
  appointments: number;
  /** Leads with engagement_score > 0. */
  engaged: number;
  /** visited / sent * 100. */
  visitRate: number;
}

export interface QualificationReport {
  qualified: QualificationCohort;
  legacy: QualificationCohort;
  /** visitRate(qualified) ÷ visitRate(legacy) — e.g. 1.9 ≈ "+90%". null when
   *  there's no legacy baseline to compare against. */
  lift: number | null;
}

interface RpcRow {
  cohort: string;
  sent: number;
  visited: number;
  appointments: number;
  engaged: number;
  visit_rate: number | null;
}

const emptyCohort = (cohort: 'qualified' | 'legacy'): QualificationCohort => ({
  cohort,
  sent: 0,
  visited: 0,
  appointments: 0,
  engaged: 0,
  visitRate: 0,
});

/** Pure projection of the RPC rows → the report shape. Unit-tested. */
export function shapeQualificationReport(rows: RpcRow[]): QualificationReport {
  const pick = (cohort: 'qualified' | 'legacy'): QualificationCohort => {
    const r = rows.find((x) => x.cohort === cohort);
    if (!r) return emptyCohort(cohort);
    return {
      cohort,
      sent: Number(r.sent) || 0,
      visited: Number(r.visited) || 0,
      appointments: Number(r.appointments) || 0,
      engaged: Number(r.engaged) || 0,
      visitRate: Number(r.visit_rate) || 0,
    };
  };
  const qualified = pick('qualified');
  const legacy = pick('legacy');
  const lift =
    legacy.visitRate > 0
      ? Math.round((qualified.visitRate / legacy.visitRate) * 10) / 10
      : null;
  return { qualified, legacy, lift };
}

export async function getQualificationReport(): Promise<QualificationReport> {
  const sb = await createSupabaseServerClient();
  const { data } = await sb.rpc('qualification_kpi_report');
  return shapeQualificationReport((data ?? []) as RpcRow[]);
}
