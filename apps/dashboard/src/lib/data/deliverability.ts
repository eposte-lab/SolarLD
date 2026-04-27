/**
 * Deliverability data helpers — server-side, RLS-scoped.
 *
 * Provides the three data shapes the /deliverability page needs:
 *
 *  1. Domain health       — one row per tenant_email_domain with pause state,
 *                           DNS verification flags, daily soft cap.
 *  2. Inbox fleet status  — one row per tenant_inbox with warmup phase,
 *                           today's send count, Smartlead health score.
 *  3. Quarantine queue    — list of content-blocked emails + aggregate counts.
 *  4. Daily send metrics  — today's sent / delivered / bounced / complained
 *                           counts rolled up from outreach_sends + events.
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface DomainHealthRow {
  id: string;
  domain: string;
  purpose: 'brand' | 'outreach';
  default_provider: string;
  tracking_host: string | null;
  // DNS verification flags (null = not checked yet)
  verified_at: string | null;
  spf_verified_at: string | null;
  dkim_verified_at: string | null;
  dmarc_verified_at: string | null;
  tracking_cname_verified_at: string | null;
  dmarc_policy: 'none' | 'quarantine' | 'reject' | null;
  daily_soft_cap: number;
  paused_until: string | null;
  pause_reason: string | null;
  active: boolean;
  created_at: string;
  // Computed client-side
  status: 'active' | 'paused' | 'inactive';
}

export interface InboxFleetRow {
  id: string;
  email: string;
  display_name: string | null;
  provider: string;
  domain_id: string | null;
  domain_name: string | null;
  daily_cap: number;
  sent_date: string | null;
  total_sent_today: number;
  last_sent_at: string | null;
  active: boolean;
  paused_until: string | null;
  pause_reason: string | null;
  warmup_started_at: string | null;
  smartlead_health_score: number | null;
  // Computed
  warmup_phase: WarmupPhase;
  effective_cap: number;
}

export type WarmupPhase =
  | 'not_started'
  | 'week_1'   // day 1-7  → 10/day
  | 'week_2'   // day 8-14 → 25/day
  | 'week_3'   // day 15-21→ 40/day
  | 'steady';  // day 22+  → 50/day

export interface QuarantineRow {
  id: string;
  lead_id: string | null;
  subject: string;
  text_snippet: string | null;
  validation_score: number;
  violations: Array<{ rule: string; field: string; detail: string; severity: string }>;
  email_style: string;
  sequence_step: number;
  review_status: 'pending_review' | 'approved' | 'rejected';
  reviewed_at: string | null;
  review_notes: string | null;
  created_at: string;
}

export interface DailySendMetrics {
  sent_today: number;
  delivered_today: number;
  bounced_today: number;
  complained_today: number;
  delivery_rate: number;   // 0-1
  complaint_rate: number;  // 0-1
}

export interface DeliverabilityData {
  domains: DomainHealthRow[];
  inboxes: InboxFleetRow[];
  quarantine_pending: QuarantineRow[];
  quarantine_pending_count: number;
  quarantine_approved_today: number;
  metrics: DailySendMetrics;
}

// ---------------------------------------------------------------------------
// Warmup phase helper
// ---------------------------------------------------------------------------

const WARMUP_CAPS: Record<WarmupPhase, number> = {
  not_started: 10,
  week_1: 10,
  week_2: 25,
  week_3: 40,
  steady: 50,
};

function computeWarmupPhase(warmupStartedAt: string | null): WarmupPhase {
  if (!warmupStartedAt) return 'not_started';
  const days = Math.floor(
    (Date.now() - new Date(warmupStartedAt).getTime()) / 86_400_000,
  );
  if (days < 7) return 'week_1';
  if (days < 14) return 'week_2';
  if (days < 21) return 'week_3';
  return 'steady';
}

function computeDomainStatus(
  row: Pick<DomainHealthRow, 'active' | 'paused_until'>,
): 'active' | 'paused' | 'inactive' {
  if (!row.active) return 'inactive';
  if (row.paused_until && row.paused_until > new Date().toISOString())
    return 'paused';
  return 'active';
}

// ---------------------------------------------------------------------------
// Main data loader
// ---------------------------------------------------------------------------

export async function getDeliverabilityData(): Promise<DeliverabilityData> {
  const sb = await createSupabaseServerClient();
  const today = new Date().toISOString().slice(0, 10); // YYYY-MM-DD
  const todayStart = `${today}T00:00:00.000Z`;

  // Fetch everything in parallel.
  const [domainsRes, inboxesRes, qPendingRes, qCountsRes, sendsRes] =
    await Promise.all([
      // 1. Domain health
      sb
        .from('tenant_email_domains')
        .select(
          'id, domain, purpose, default_provider, tracking_host, ' +
            'verified_at, spf_verified_at, dkim_verified_at, ' +
            'dmarc_verified_at, tracking_cname_verified_at, dmarc_policy, ' +
            'daily_soft_cap, paused_until, pause_reason, active, created_at',
        )
        .order('purpose')
        .order('domain'),

      // 2. Inbox fleet — joined with domain name for display
      sb
        .from('tenant_inboxes')
        .select(
          'id, email, display_name, provider, domain_id, ' +
            'daily_cap, sent_date, total_sent_today, last_sent_at, ' +
            'active, paused_until, pause_reason, warmup_started_at, ' +
            'smartlead_health_score, ' +
            'tenant_email_domains!tenant_inboxes_domain_id_fkey(domain)',
        )
        .order('email'),

      // 3. Quarantine — pending_review items (last 50)
      sb
        .from('quarantine_emails')
        .select(
          'id, lead_id, subject, text_snippet, validation_score, ' +
            'violations, email_style, sequence_step, review_status, ' +
            'reviewed_at, review_notes, created_at',
        )
        .eq('review_status', 'pending_review')
        .order('created_at', { ascending: false })
        .limit(50),

      // 4. Quarantine counts (pending total + approved today)
      Promise.all([
        sb
          .from('quarantine_emails')
          .select('id', { count: 'exact', head: true })
          .eq('review_status', 'pending_review'),
        sb
          .from('quarantine_emails')
          .select('id', { count: 'exact', head: true })
          .eq('review_status', 'approved')
          .gte('reviewed_at', todayStart),
      ]),

      // 5. Today's send metrics from outreach_sends
      sb
        .from('outreach_sends')
        .select('status, failure_reason')
        .gte('sent_at', todayStart),
    ]);

  // ---------- Domains ----------
  const domains: DomainHealthRow[] = ((domainsRes.data ?? []) as unknown as Omit<
    DomainHealthRow,
    'status'
  >[]).map((d) => ({
    ...d,
    status: computeDomainStatus(d as Pick<DomainHealthRow, 'active' | 'paused_until'>),
  }));

  // ---------- Inboxes ----------
  const inboxes: InboxFleetRow[] = ((inboxesRes.data ?? []) as unknown as Record<
    string,
    unknown
  >[]).map((raw) => {
    const r = raw;
    const domJoin = r['tenant_email_domains'] as Record<string, string> | null;
    const phase = computeWarmupPhase(r['warmup_started_at'] as string | null);
    return {
      id: r['id'] as string,
      email: r['email'] as string,
      display_name: r['display_name'] as string | null,
      provider: (r['provider'] as string) ?? 'resend',
      domain_id: r['domain_id'] as string | null,
      domain_name: domJoin?.['domain'] ?? null,
      daily_cap: (r['daily_cap'] as number) ?? 50,
      sent_date: r['sent_date'] as string | null,
      total_sent_today: (r['total_sent_today'] as number) ?? 0,
      last_sent_at: r['last_sent_at'] as string | null,
      active: Boolean(r['active']),
      paused_until: r['paused_until'] as string | null,
      pause_reason: r['pause_reason'] as string | null,
      warmup_started_at: r['warmup_started_at'] as string | null,
      smartlead_health_score: r['smartlead_health_score'] as number | null,
      warmup_phase: phase,
      effective_cap: WARMUP_CAPS[phase],
    };
  });

  // ---------- Quarantine ----------
  const quarantine_pending: QuarantineRow[] = (qPendingRes.data ?? []) as unknown as QuarantineRow[];
  const [pendingCountRes, approvedTodayRes] = qCountsRes;
  const quarantine_pending_count = pendingCountRes.count ?? 0;
  const quarantine_approved_today = approvedTodayRes.count ?? 0;

  // ---------- Daily metrics ----------
  const sends = sendsRes.data ?? [];
  const sent_today = sends.length;
  const delivered_today = sends.filter((s) => s.status === 'delivered').length;
  const bounced_today = sends.filter(
    (s) => s.failure_reason === 'bounced',
  ).length;
  const complained_today = sends.filter(
    (s) => s.failure_reason === 'complained',
  ).length;

  const metrics: DailySendMetrics = {
    sent_today,
    delivered_today,
    bounced_today,
    complained_today,
    delivery_rate: sent_today > 0 ? delivered_today / sent_today : 0,
    complaint_rate: sent_today > 0 ? complained_today / sent_today : 0,
  };

  return {
    domains,
    inboxes,
    quarantine_pending,
    quarantine_pending_count,
    quarantine_approved_today,
    metrics,
  };
}
