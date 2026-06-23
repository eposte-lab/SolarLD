/**
 * Operational pipeline status — the "a che punto siamo" snapshot.
 *
 * Surfaces the live operational health an operator otherwise has to ask for:
 * how many renders are stuck, how many leads are ready in the warehouse, how
 * many went out today vs the daily cap. Rendered as a card at the top of
 * /funnel. All counts are RLS-scoped to the current tenant.
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';

/** Pipeline states that don't need a render / aren't "live" anymore. */
const TERMINAL = ['blacklisted', 'closed_won', 'closed_lost', 'expired'];
const ACTIVE = ['engaged', 'to_call', 'appointment', 'whatsapp'];

export interface OperationalStatus {
  /** Leads whose render is done (image present). */
  renderDone: number;
  /** Leads with a render FAILURE and no image yet (non-terminal). The
   *  render-retry cron auto-reattempts these. */
  renderStuck: number;
  /** Leads in the warehouse, ready_to_send (with or without image). */
  warehouseReady: number;
  /** Ready AND with a render image — sendable on the next pass. */
  sendableNow: number;
  /** Currently mid-send (picked). */
  picked: number;
  /** Cold sends that left today (UTC day). */
  sentToday: number;
  /** Tenant daily cold-send cap. */
  dailyCap: number;
  /** Active/engaged leads (engaged, da chiamare, appuntamento, whatsapp). */
  active: number;
}

export type StatusTone = 'ok' | 'info' | 'warn' | 'danger';
export interface StatusFlag {
  tone: StatusTone;
  text: string;
}

/**
 * Pure: turn the raw counts into the operator-facing alert chips. Kept
 * separate from the queries so it's unit-testable.
 */
export function deriveStatusFlags(s: OperationalStatus): StatusFlag[] {
  const flags: StatusFlag[] = [];
  if (s.renderStuck > 0) {
    flags.push({
      tone: 'danger',
      text: `${s.renderStuck} render bloccati — riprovo in automatico ogni 10 min`,
    });
  }
  if (s.warehouseReady === 0) {
    flags.push({ tone: 'warn', text: 'Magazzino vuoto — nessun lead pronto all’invio' });
  }
  if (s.dailyCap > 0 && s.sentToday >= s.dailyCap) {
    flags.push({
      tone: 'info',
      text: `Cap giornaliero raggiunto (${s.sentToday}/${s.dailyCap})`,
    });
  }
  if (flags.length === 0) {
    flags.push({ tone: 'ok', text: 'Tutto regolare' });
  }
  return flags;
}

function startOfUtcDayIso(now: Date): string {
  const d = new Date(now);
  d.setUTCHours(0, 0, 0, 0);
  return d.toISOString();
}

export async function getOperationalStatus(): Promise<OperationalStatus> {
  const sb = await createSupabaseServerClient();
  const head = () => sb.from('leads').select('id', { count: 'exact', head: true });
  const todayIso = startOfUtcDayIso(new Date());
  const terminalList = `(${TERMINAL.join(',')})`;

  const [done, stuck, ready, sendable, picked, sent, active] = await Promise.all([
    head().not('rendering_image_url', 'is', null),
    head()
      .is('rendering_image_url', null)
      .not('creative_skipped_reason', 'is', null)
      .not('pipeline_status', 'in', terminalList),
    head().eq('pipeline_status', 'ready_to_send'),
    head().eq('pipeline_status', 'ready_to_send').not('rendering_image_url', 'is', null),
    head().eq('pipeline_status', 'picked'),
    head().gte('outreach_sent_at', todayIso),
    head().in('pipeline_status', ACTIVE),
  ]);

  const capRes = await sb
    .from('tenants')
    .select('daily_target_send_cap')
    .limit(1)
    .maybeSingle();
  const dailyCap = Number(
    (capRes.data as { daily_target_send_cap?: number } | null)?.daily_target_send_cap ?? 0,
  );

  return {
    renderDone: done.count ?? 0,
    renderStuck: stuck.count ?? 0,
    warehouseReady: ready.count ?? 0,
    sendableNow: sendable.count ?? 0,
    picked: picked.count ?? 0,
    sentToday: sent.count ?? 0,
    dailyCap,
    active: active.count ?? 0,
  };
}
