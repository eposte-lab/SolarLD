'use client';

/**
 * Step 4 — Territorio + Budget.
 *
 * Two blocks:
 *   a) Priority zones (multi-select chips) — gives Hunter a spatial
 *      preference order when it queues scan jobs.
 *   b) Budget caps (two EUR sliders) — hard ceiling per month on
 *      scan + outreach spend. Enforced later inside the worker loop
 *      against `api_usage_log`.
 */

import { cn } from '@/lib/utils';

import { PRIORITY_ZONES, type WizardForm } from './wizard-types';

export interface Step4Props {
  form: WizardForm;
  onChange: (f: WizardForm) => void;
}

function formatEur(v: number): string {
  return `€${v.toLocaleString('it-IT', { maximumFractionDigits: 0 })}`;
}

export function Step4Territory({ form, onChange }: Step4Props) {
  function toggleZone(value: string) {
    const has = form.scan_priority_zones.includes(value);
    const next = has
      ? form.scan_priority_zones.filter((z) => z !== value)
      : [...form.scan_priority_zones, value];
    if (next.length === 0) return; // keep at least one
    onChange({ ...form, scan_priority_zones: next });
  }

  return (
    <section className="space-y-6">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Passo 4 di 5
        </p>
        <h2 className="mt-1 font-headline text-3xl font-bold tracking-tighter">
          Territorio & budget
        </h2>
        <p className="mt-2 max-w-xl text-sm text-on-surface-variant">
          Dove scansioniamo per primo e quanto sei disposto a spendere ogni
          mese.
        </p>
      </header>

      {/* Priority zones */}
      <div className="rounded-xl bg-surface-container-lowest p-6 shadow-ambient-sm">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Zone prioritarie
        </p>
        <p className="mt-1 text-sm text-on-surface-variant">
          Hunter scansiona queste aree per prime. Almeno una richiesta.
        </p>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {PRIORITY_ZONES.map((z) => {
            const active = form.scan_priority_zones.includes(z.value);
            return (
              <button
                key={z.value}
                type="button"
                onClick={() => toggleZone(z.value)}
                className={cn(
                  'flex flex-col items-start gap-1 rounded-xl p-4 text-left transition-all',
                  active
                    ? 'bg-primary text-on-primary shadow-ambient-sm ring-2 ring-primary/30'
                    : 'bg-surface-container-low text-on-surface hover:bg-surface-container',
                )}
              >
                <span className="text-sm font-semibold">{z.label}</span>
                <span
                  className={cn(
                    'text-xs',
                    active ? 'text-on-primary/80' : 'text-on-surface-variant',
                  )}
                >
                  {z.hint}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Budget caps */}
      <div className="grid gap-4 md:grid-cols-2">
        <BudgetCard
          label="Budget scansione mensile"
          description="Spesa massima mensile in Google Places + Google Solar + Atoka."
          value={form.monthly_scan_budget_eur}
          min={100}
          max={10000}
          step={100}
          onChange={(v) => onChange({ ...form, monthly_scan_budget_eur: v })}
        />
        <BudgetCard
          label="Budget outreach mensile"
          description="Costo Resend + NeverBounce + cartoline B2C (se attive)."
          value={form.monthly_outreach_budget_eur}
          min={100}
          max={10000}
          step={100}
          onChange={(v) =>
            onChange({ ...form, monthly_outreach_budget_eur: v })
          }
        />
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------

interface BudgetCardProps {
  label: string;
  description: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}

function BudgetCard({
  label,
  description,
  value,
  min,
  max,
  step,
  onChange,
}: BudgetCardProps) {
  return (
    <div className="rounded-xl bg-surface-container-lowest p-6 shadow-ambient-sm">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm font-semibold">{label}</p>
          <p className="mt-1 text-xs text-on-surface-variant">{description}</p>
        </div>
        <span className="font-headline text-3xl font-bold tabular-nums text-primary">
          {formatEur(value)}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="mt-4 w-full accent-primary"
      />
      <div className="mt-1 flex justify-between text-[10px] font-medium uppercase tracking-widest text-on-surface-variant">
        <span>{formatEur(min)}</span>
        <span>{formatEur(max)}</span>
      </div>
    </div>
  );
}
