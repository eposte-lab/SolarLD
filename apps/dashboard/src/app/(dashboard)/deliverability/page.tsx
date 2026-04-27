/**
 * Deliverability — admin view for domain health, inbox fleet,
 * send metrics, and quarantine review queue.
 *
 * Three sections:
 *   1. KPI strip — active domains, warmup inboxes, sends today, pending reviews
 *   2. Domain health table — DNS verification, pause state, Smartlead score
 *   3. Inbox fleet table — warmup phase, daily cap, sent today, health score
 *   4. Quarantine queue — pending items with approve/reject actions
 */

import { redirect } from 'next/navigation';

import { QuarantineActions } from '@/components/deliverability/quarantine-actions';
import { BadgeStatus } from '@/components/ui/badge-status';
import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { KpiChipCard } from '@/components/ui/kpi-chip-card';
import { SectionEyebrow } from '@/components/ui/section-eyebrow';
import { getDeliverabilityData, type WarmupPhase } from '@/lib/data/deliverability';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { cn, relativeTime } from '@/lib/utils';

export const dynamic = 'force-dynamic';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const WARMUP_LABEL: Record<WarmupPhase, string> = {
  not_started: 'Non avviato',
  week_1: 'Ramp-up sett. 1',
  week_2: 'Ramp-up sett. 2',
  week_3: 'Ramp-up sett. 3',
  steady: 'Regime',
};

// Editorial Glass — single-accent: tutto warmup vira amber, "steady" success.
const WARMUP_COLOR: Record<WarmupPhase, string> = {
  not_started: 'bg-white/8 text-on-surface-variant',
  week_1: 'bg-primary/10 text-primary',
  week_2: 'bg-primary/15 text-primary',
  week_3: 'bg-primary/20 text-primary',
  steady: 'bg-success/15 text-success',
};

function DnsCheck({ ok }: { ok: boolean }) {
  return ok ? (
    <span className="inline-block h-2 w-2 rounded-full bg-success" title="Verificato" />
  ) : (
    <span className="inline-block h-2 w-2 rounded-full bg-error" title="Non verificato" />
  );
}

function SmartleadScore({ score }: { score: number | null }) {
  if (score === null)
    return <span className="text-on-surface-variant text-xs">—</span>;
  const color =
    score >= 70
      ? 'text-success'
      : score >= 40
        ? 'text-primary'
        : 'text-error';
  return <span className={cn('font-semibold tabular-nums text-sm', color)}>{score.toFixed(0)}</span>;
}

function StatusChip({ status }: { status: 'active' | 'paused' | 'inactive' }) {
  if (status === 'active') return <BadgeStatus tone="success" label="Attivo" />;
  if (status === 'paused') return <BadgeStatus tone="warning" label="Sospeso" />;
  return <BadgeStatus tone="neutral" label="Inattivo" dotless />;
}

function ViolationBadge({ severity }: { severity: string }) {
  return (
    <span
      className={cn(
        'rounded px-1.5 py-0.5 text-[10px] font-bold uppercase',
        severity === 'block'
          ? 'bg-error/15 text-error'
          : 'bg-primary/15 text-primary',
      )}
    >
      {severity}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default async function DeliverabilityPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const data = await getDeliverabilityData();

  const activeDomains = data.domains.filter((d) => d.status === 'active').length;
  const pausedDomains = data.domains.filter((d) => d.status === 'paused').length;
  const warmingInboxes = data.inboxes.filter(
    (i) => i.active && i.warmup_phase !== 'steady' && i.warmup_phase !== 'not_started',
  ).length;
  const steadyInboxes = data.inboxes.filter(
    (i) => i.active && i.warmup_phase === 'steady',
  ).length;

  const { metrics } = data;

  return (
    <div className="space-y-8">
      {/* ── Header ─────────────────────────────────────────────────── */}
      <header className="flex flex-col gap-2">
        <SectionEyebrow>Operational efficiency · Domain health</SectionEyebrow>
        <h1 className="font-headline text-5xl font-bold leading-[1.05] tracking-tightest text-on-surface">
          Deliverability
        </h1>
        <p className="text-sm text-on-surface-variant">
          Stato domini · Inbox in warm-up · Metriche invio · Coda quarantine
        </p>
      </header>

      {/* ── KPI Strip ──────────────────────────────────────────────── */}
      <BentoGrid cols={4}>
        <KpiChipCard
          label="Inviati oggi"
          value={metrics.sent_today}
          hint={
            metrics.sent_today > 0
              ? `${(metrics.delivery_rate * 100).toFixed(0)}% consegnati`
              : 'Nessun invio'
          }
          trend={
            metrics.complaint_rate > 0
              ? { delta: -(metrics.complaint_rate * 100), unit: '% complain' }
              : undefined
          }
          tone="highlight"
          size="hero"
          className="md:col-span-2"
        />
        <KpiChipCard
          label="Domini attivi"
          value={activeDomains}
          hint={pausedDomains > 0 ? `${pausedDomains} sospeso` : `di ${data.domains.length} totali`}
          tone={pausedDomains > 0 ? 'critical' : 'success'}
        />
        <KpiChipCard
          label="Inbox in warm-up"
          value={warmingInboxes}
          hint={`${steadyInboxes} a regime · ${data.inboxes.length} totali`}
          tone="neutral"
        />
        <KpiChipCard
          label="Quarantine da revisionare"
          value={data.quarantine_pending_count}
          hint={
            data.quarantine_approved_today > 0
              ? `${data.quarantine_approved_today} approvate oggi`
              : 'Nessuna revisione oggi'
          }
          tone={data.quarantine_pending_count > 0 ? 'critical' : 'success'}
          className="md:col-span-2"
        />
      </BentoGrid>

      {/* ── Domain Health ──────────────────────────────────────────── */}
      <BentoCard span="full">
        <h2 className="mb-5 font-headline text-lg font-bold tracking-tight text-on-surface">
          Salute dei domini
        </h2>

        {data.domains.length === 0 ? (
          <p className="text-sm text-on-surface-variant">
            Nessun dominio configurato.{' '}
            <a href="/settings/email-domains" className="text-primary underline">
              Aggiungi un dominio outreach →
            </a>
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-surface-container-high text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  <th className="pb-3 text-left">Dominio</th>
                  <th className="pb-3 text-left">Scopo</th>
                  <th className="pb-3 text-center">SPF</th>
                  <th className="pb-3 text-center">DKIM</th>
                  <th className="pb-3 text-center">DMARC</th>
                  <th className="pb-3 text-center">Tracking</th>
                  <th className="pb-3 text-right">Cap/day</th>
                  <th className="pb-3 text-right">Stato</th>
                </tr>
              </thead>
              <tbody>
                {data.domains.map((d) => (
                  <tr
                    key={d.id}
                    className="border-b border-surface-container-low last:border-0"
                  >
                    <td className="py-3">
                      <span className="font-mono text-xs font-semibold text-on-surface">
                        {d.domain}
                      </span>
                      {d.pause_reason && d.status === 'paused' && (
                        <p className="mt-0.5 text-[10px] text-on-surface-variant">
                          {d.pause_reason.replace(/_/g, ' ')}
                        </p>
                      )}
                    </td>
                    <td className="py-3">
                      <span className="text-xs text-on-surface-variant">
                        {d.purpose === 'brand' ? 'Brand' : 'Outreach'}
                      </span>
                    </td>
                    <td className="py-3 text-center">
                      <DnsCheck ok={!!d.spf_verified_at} />
                    </td>
                    <td className="py-3 text-center">
                      <DnsCheck ok={!!d.dkim_verified_at} />
                    </td>
                    <td className="py-3 text-center">
                      <DnsCheck ok={!!d.dmarc_verified_at} />
                    </td>
                    <td className="py-3 text-center">
                      <DnsCheck ok={!!d.tracking_cname_verified_at} />
                    </td>
                    <td className="py-3 text-right tabular-nums text-on-surface-variant">
                      {d.daily_soft_cap.toLocaleString('it-IT')}
                    </td>
                    <td className="py-3 text-right">
                      <StatusChip status={d.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </BentoCard>

      {/* ── Inbox Fleet ────────────────────────────────────────────── */}
      <BentoCard span="full">
        <h2 className="mb-5 font-headline text-lg font-bold tracking-tight text-on-surface">
          Fleet inbox — warm-up & utilizzo oggi
        </h2>

        {data.inboxes.length === 0 ? (
          <p className="text-sm text-on-surface-variant">
            Nessuna inbox configurata.{' '}
            <a href="/settings/inboxes" className="text-primary underline">
              Aggiungi inbox →
            </a>
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-surface-container-high text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  <th className="pb-3 text-left">Inbox</th>
                  <th className="pb-3 text-left">Dominio</th>
                  <th className="pb-3 text-left">Fase</th>
                  <th className="pb-3 text-right">Inviati / Cap</th>
                  <th className="pb-3 text-right">Smartlead</th>
                  <th className="pb-3 text-right">Ultimo invio</th>
                  <th className="pb-3 text-right">Stato</th>
                </tr>
              </thead>
              <tbody>
                {data.inboxes.map((inbox) => {
                  const today = new Date().toISOString().slice(0, 10);
                  const sentToday =
                    inbox.sent_date === today ? inbox.total_sent_today : 0;
                  const cap = inbox.effective_cap;
                  const pct = cap > 0 ? (sentToday / cap) * 100 : 0;

                  const inboxStatus = !inbox.active
                    ? 'inactive'
                    : inbox.paused_until &&
                        inbox.paused_until > new Date().toISOString()
                      ? 'paused'
                      : 'active';

                  return (
                    <tr
                      key={inbox.id}
                      className="border-b border-surface-container-low last:border-0"
                    >
                      <td className="py-3">
                        <p className="text-xs font-semibold text-on-surface">
                          {inbox.display_name || inbox.email.split('@')[0]}
                        </p>
                        <p className="text-[10px] text-on-surface-variant">
                          {inbox.email}
                        </p>
                      </td>
                      <td className="py-3 text-xs text-on-surface-variant">
                        {inbox.domain_name ?? '—'}
                      </td>
                      <td className="py-3">
                        <span
                          className={cn(
                            'rounded-full px-2 py-0.5 text-[10px] font-semibold',
                            WARMUP_COLOR[inbox.warmup_phase],
                          )}
                        >
                          {WARMUP_LABEL[inbox.warmup_phase]}
                        </span>
                      </td>
                      <td className="py-3 text-right">
                        <div className="flex items-center justify-end gap-2">
                          <div className="h-1.5 w-20 overflow-hidden rounded-full bg-white/8">
                            <div
                              className={cn(
                                'h-full rounded-full transition-all',
                                pct >= 95
                                  ? 'bg-error'
                                  : pct >= 70
                                    ? 'bg-primary'
                                    : 'bg-success',
                              )}
                              style={{ width: `${Math.min(100, pct).toFixed(0)}%` }}
                            />
                          </div>
                          <span className="min-w-[60px] text-right tabular-nums text-xs text-on-surface-variant">
                            {sentToday} / {cap}
                          </span>
                        </div>
                      </td>
                      <td className="py-3 text-right">
                        <SmartleadScore score={inbox.smartlead_health_score} />
                      </td>
                      <td className="py-3 text-right text-xs text-on-surface-variant">
                        {inbox.last_sent_at
                          ? relativeTime(inbox.last_sent_at)
                          : '—'}
                      </td>
                      <td className="py-3 text-right">
                        <StatusChip status={inboxStatus} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </BentoCard>

      {/* ── Quarantine Queue ───────────────────────────────────────── */}
      <BentoCard span="full">
        <div className="mb-5 flex items-center justify-between">
          <div>
            <h2 className="font-headline text-lg font-bold tracking-tight text-on-surface">
              Coda quarantine
            </h2>
            <p className="text-xs text-on-surface-variant">
              Email bloccate dal validatore di contenuto — in attesa di revisione
            </p>
          </div>
          {data.quarantine_pending_count > 0 && (
            <BadgeStatus tone="warning" label={`${data.quarantine_pending_count} in attesa`} />
          )}
        </div>

        {data.quarantine_pending.length === 0 ? (
          <div className="rounded-2xl glass-panel-sm p-6 text-center">
            <p className="text-sm font-semibold text-success">
              ✓ Nessun elemento in coda
            </p>
            <p className="mt-1 text-xs text-on-surface-variant">
              Tutti i contenuti hanno superato la validazione
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {data.quarantine_pending.map((item) => (
              <div
                key={item.id}
                className="rounded-xl bg-surface-container-low p-4"
              >
                <div className="flex items-start justify-between gap-4">
                  {/* Left: subject + snippet */}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="truncate text-sm font-semibold text-on-surface">
                        {item.subject}
                      </p>
                      <span className="shrink-0 rounded bg-surface-container px-1.5 py-0.5 text-[10px] text-on-surface-variant">
                        step {item.sequence_step} · {item.email_style}
                      </span>
                    </div>
                    {item.text_snippet && (
                      <p className="mt-1 line-clamp-2 text-xs text-on-surface-variant">
                        {item.text_snippet}
                      </p>
                    )}
                    {/* Violations */}
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {(item.violations ?? []).map((v, i) => (
                        <span
                          key={i}
                          className="inline-flex items-center gap-1 rounded bg-surface-container px-1.5 py-0.5 text-[10px] text-on-surface-variant"
                        >
                          <ViolationBadge severity={v.severity} />
                          <span>{v.rule}</span>
                        </span>
                      ))}
                    </div>
                  </div>

                  {/* Right: score + date + actions */}
                  <div className="flex shrink-0 flex-col items-end gap-2">
                    <div className="text-right">
                      <SectionEyebrow tone="dim">Score</SectionEyebrow>
                      <p
                        className={cn(
                          'font-headline text-xl font-bold tabular-nums tracking-tightest',
                          item.validation_score >= 1
                            ? 'text-error'
                            : 'text-primary',
                        )}
                      >
                        {(item.validation_score * 100).toFixed(0)}
                      </p>
                    </div>
                    <p className="text-[10px] text-on-surface-variant">
                      {relativeTime(item.created_at)}
                    </p>
                    <QuarantineActions
                      quarantineId={item.id}
                      reviewStatus={item.review_status}
                    />
                  </div>
                </div>
              </div>
            ))}

            {data.quarantine_pending_count > data.quarantine_pending.length && (
              <p className="text-center text-xs text-on-surface-variant">
                Mostrati i primi 50 su {data.quarantine_pending_count}. Usa{' '}
                <code className="rounded bg-surface-container px-1">GET /v1/quarantine</code>{' '}
                per esportare tutti.
              </p>
            )}
          </div>
        )}
      </BentoCard>
    </div>
  );
}
