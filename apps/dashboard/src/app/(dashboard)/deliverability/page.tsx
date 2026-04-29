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

import { DomainHealthTable } from '@/components/deliverability/domain-health-table';
import { InboxFleetTable } from '@/components/deliverability/inbox-fleet-table';
import { QuarantineActions } from '@/components/deliverability/quarantine-actions';
import { BadgeStatus } from '@/components/ui/badge-status';
import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { KpiChipCard } from '@/components/ui/kpi-chip-card';
import { SectionEyebrow } from '@/components/ui/section-eyebrow';
import { getDeliverabilityData } from '@/lib/data/deliverability';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { cn, relativeTime } from '@/lib/utils';

export const dynamic = 'force-dynamic';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
          <DomainHealthTable rows={data.domains} />
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
          <InboxFleetTable rows={data.inboxes} />
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
