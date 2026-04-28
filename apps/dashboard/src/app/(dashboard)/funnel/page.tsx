/**
 * Funnel — waterfall completo dall'Atoka discovery alla firma del contratto.
 *
 * Struttura in due blocchi:
 *   1. Discovery (top-of-funnel):   L1 Atoka → L2 Enrichment → L3 Score → L4 Solar
 *   2. Pipeline (post-discovery):   Lead → Inviato → Consegnato → Aperto → Cliccato
 *                                   → Engaged → Appuntamento → Vinto
 *
 * Per ogni step mostra:
 *   - Count assoluto
 *   - Drop-off rispetto allo step precedente (% pass-through)
 *   - Linea visuale di riduzione (bar proporzionale)
 *
 * La strip con le metriche di costo (€/contatto, €/lead, €/inviato, spesa
 * totale) è stata rimossa: l'installatore paga una tariffa flat e i costi
 * per-scan sono un dettaglio di back-office, non un'informazione
 * commercialmente rilevante. La spesa resta visibile solo a ops via
 * `/v1/admin/cost-report`.
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { BentoCard } from '@/components/ui/bento-card';
import { getScanFunnel } from '@/lib/data/contatti';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { cn, formatNumber, formatPercent } from '@/lib/utils';

export const dynamic = 'force-dynamic';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pct(num: number, denom: number): string {
  if (!denom || !num) return '—';
  return formatPercent(num / denom, 0);
}

function drop(num: number, denom: number): string {
  if (!denom) return '—';
  const d = denom - num;
  return `-${formatNumber(d)} (${formatPercent(d / denom, 0)})`;
}

function barWidth(num: number, max: number): string {
  if (!max) return '0%';
  return `${Math.max(2, Math.round((num / max) * 100))}%`;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default async function FunnelPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const funnel = await getScanFunnel();
  const { discovery: d, pipeline: p } = funnel;

  const maxDisc = d.l1 || 1;
  const maxPipe = p.leads_total || 1;

  return (
    <div className="space-y-8">
      {/* Header */}
      <header className="flex flex-col gap-1">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Funnel completo · Discovery → Pipeline → Chiusura
        </p>
        <h1 className="font-headline text-4xl font-bold tracking-tighter">
          Funnel
        </h1>
      </header>

      {/* Discovery block */}
      <BentoCard span="full">
        <header className="mb-6 flex items-end justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Discovery · Funnel B2B
            </p>
            <h2 className="font-headline text-2xl font-bold tracking-tighter">
              Top-of-funnel (scan)
            </h2>
          </div>
          <Link
            href={'/contatti'}
            className="text-xs font-semibold text-primary hover:underline"
          >
            Tutti i contatti →
          </Link>
        </header>

        <div className="space-y-3">
          {[
            {
              label: 'L1 — Scoperte (database aziende)',
              value: d.l1,
              prev: null,
              href: '/contatti?stage=1',
              accent: 'neutral' as const,
            },
            {
              label: 'L2 — Dati arricchiti',
              value: d.l2,
              prev: d.l1,
              href: '/contatti?stage=2',
              accent: 'primary' as const,
            },
            {
              label: 'L3 — Punteggio assegnato',
              value: d.l3,
              prev: d.l2,
              href: '/contatti?stage=3',
              accent: 'tertiary' as const,
            },
            {
              label: 'L4 — Tetto idoneo',
              value: d.l4_qualified,
              prev: d.l3,
              href: '/contatti?stage=4',
              accent: 'secondary' as const,
            },
          ].map((step) => (
            <WaterfallRow
              key={step.label}
              label={step.label}
              value={step.value}
              prev={step.prev}
              maxValue={maxDisc}
              href={step.href}
            />
          ))}

          {/* L4 sub-breakdown */}
          {(d.l4_rejected > 0 || d.l4_skipped > 0) && (
            <div className="ml-8 mt-2 space-y-2 border-l-2 border-outline-variant/20 pl-4">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                L4 — altri verdetti Solar
              </p>
              <div className="flex flex-wrap gap-4 text-xs text-on-surface-variant">
                <span>
                  Rifiutate (tecnico):{' '}
                  <strong>{formatNumber(d.l4_rejected)}</strong>
                </span>
                <span>
                  Skip (gate score):{' '}
                  <strong>{formatNumber(d.l4_skipped)}</strong>
                </span>
              </div>
            </div>
          )}
        </div>
      </BentoCard>

      {/* Pipeline block */}
      <BentoCard span="full">
        <header className="mb-6 flex items-end justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Pipeline · Post-qualificazione
            </p>
            <h2 className="font-headline text-2xl font-bold tracking-tighter">
              Outreach → Chiusura
            </h2>
          </div>
          <Link
            href="/leads"
            className="text-xs font-semibold text-primary hover:underline"
          >
            Lead attivi →
          </Link>
        </header>

        <div className="space-y-3">
          {[
            {
              label: 'Lead qualificati (in pipeline)',
              value: p.leads_total,
              prev: d.l4_qualified || null,
              prevLabel: 'vs. L4 Solar',
              href: '/leads',
            },
            {
              label: 'Inviati (primo outreach)',
              value: p.sent,
              prev: p.leads_total,
              href: '/invii',
            },
            {
              label: 'Consegnati',
              value: p.delivered,
              prev: p.sent,
              href: '/invii?status=delivered',
            },
            {
              label: 'Aperti (almeno 1 apertura)',
              value: p.opened,
              prev: p.delivered,
              href: '/leads?status=opened',
            },
            {
              label: 'Cliccati (CTA portal)',
              value: p.clicked,
              prev: p.opened,
              href: '/leads?status=clicked',
            },
            {
              label: 'Engaged (alto interesse)',
              value: p.engaged,
              prev: p.clicked,
              href: '/leads?status=engaged',
            },
            {
              label: 'Appuntamenti fissati',
              value: p.appointment,
              prev: p.engaged,
              href: '/leads?status=appointment',
            },
            {
              label: 'Contratti vinti',
              value: p.won,
              prev: p.appointment,
              href: '/leads?status=closed_won',
            },
          ].map((step) => (
            <WaterfallRow
              key={step.label}
              label={step.label}
              value={step.value}
              prev={step.prev ?? null}
              maxValue={maxPipe}
              href={step.href}
              highlight={step.label.includes('Contratti')}
            />
          ))}
        </div>

        {/* Conversion rates summary */}
        <div className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-4">
          {[
            { label: 'Lead → Inviati', n: p.sent, d: p.leads_total },
            { label: 'Inviati → Aperti', n: p.opened, d: p.sent },
            { label: 'Aperti → Engaged', n: p.engaged, d: p.opened },
            { label: 'Engaged → Vinti', n: p.won, d: p.engaged },
          ].map((r) => (
            <div
              key={r.label}
              className="rounded-lg bg-surface-container-low p-4"
            >
              <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                {r.label}
              </p>
              <p className="mt-2 font-headline text-2xl font-bold leading-none tracking-tighter">
                {pct(r.n, r.d)}
              </p>
              <p className="mt-1 text-xs text-on-surface-variant">
                {formatNumber(r.n)} / {formatNumber(r.d)}
              </p>
            </div>
          ))}
        </div>
      </BentoCard>

      {/* Conversion tracking note */}
      {p.conversions_won > 0 && (
        <BentoCard span="full">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Conversioni tracciato pixel
          </p>
          <p className="mt-1 font-headline text-2xl font-bold tracking-tighter text-primary">
            {formatNumber(p.conversions_won)} contratti vinti (pixel)
          </p>
          <p className="mt-2 text-sm text-on-surface-variant">
            Tracciati via{' '}
            <code className="rounded bg-surface-container px-1 font-mono text-xs">
              conversions.stage=won
            </code>{' '}
            — includono sia input manuale che CRM webhook e pixel.
          </p>
        </BentoCard>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// WaterfallRow — single bar with count + pass-through
// ---------------------------------------------------------------------------

function WaterfallRow({
  label,
  value,
  prev,
  maxValue,
  href,
  highlight = false,
}: {
  label: string;
  value: number;
  prev: number | null;
  maxValue: number;
  href: string;
  highlight?: boolean;
}) {
  const width = barWidth(value, maxValue);
  const passThrough = prev != null ? pct(value, prev) : null;
  const dropped = prev != null && prev > 0 ? drop(value, prev) : null;

  return (
    <Link href={href} className="group block">
      <div className="flex items-center gap-4 rounded-lg p-3 transition-colors hover:bg-surface-container-low">
        {/* Label + count */}
        <div className="w-64 shrink-0">
          <p
            className={cn(
              'text-sm font-semibold',
              highlight ? 'text-primary' : 'text-on-surface',
            )}
          >
            {label}
          </p>
          {dropped && (
            <p className="text-[10px] text-on-surface-variant">{dropped}</p>
          )}
        </div>

        {/* Bar */}
        <div className="flex-1 rounded-full bg-surface-container-high">
          <div
            className={cn(
              'h-3 rounded-full transition-all',
              highlight
                ? 'bg-primary'
                : 'bg-primary/60 group-hover:bg-primary/80',
            )}
            style={{ width }}
          />
        </div>

        {/* Number + rate */}
        <div className="w-32 shrink-0 text-right">
          <span
            className={cn(
              'font-headline text-xl font-bold tabular-nums',
              highlight ? 'text-primary' : 'text-on-surface',
            )}
          >
            {formatNumber(value)}
          </span>
          {passThrough && (
            <span className="ml-2 text-xs text-on-surface-variant">
              {passThrough}
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}
