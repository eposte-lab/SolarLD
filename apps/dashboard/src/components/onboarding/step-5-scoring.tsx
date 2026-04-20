'use client';

/**
 * Step 5 — Scoring threshold + final review.
 *
 * Two blocks:
 *   a) Four selectable "threshold bucket" cards. We don't expose the raw
 *      0–100 slider because installers think in shorthand ("aggressive"
 *      vs "elite"); the numeric value is what hits the API.
 *   b) Review panel — condensed recap of every wizard choice so the
 *      user can sanity-check before the submit button. Labels match
 *      the step summaries (not the DB column names) so it reads like
 *      a quote, not a schema dump.
 */

import { cn } from '@/lib/utils';

import { THRESHOLD_BUCKETS, type WizardForm } from './wizard-types';

export interface Step5Props {
  form: WizardForm;
  onChange: (f: WizardForm) => void;
}

const SCAN_MODE_LABELS: Record<WizardForm['scan_mode'], string> = {
  b2b_precision: 'B2B Precision',
  opportunistic: 'Opportunistic',
  volume: 'Volume Play',
};

const ZONE_LABELS: Record<string, string> = {
  capoluoghi: 'Capoluoghi',
  costa: 'Costa',
  zone_industriali: 'Zone industriali',
  provincia: 'Provincia',
};

function formatEur(v: number): string {
  return `€${v.toLocaleString('it-IT', { maximumFractionDigits: 0 })}`;
}

function formatPercent(v: number): string {
  return `${Math.round(v * 100)}%`;
}

export function Step5Scoring({ form, onChange }: Step5Props) {
  return (
    <section className="space-y-6">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Passo 5 di 5
        </p>
        <h2 className="mt-1 font-headline text-3xl font-bold tracking-tighter">
          Soglia di qualità & riepilogo
        </h2>
        <p className="mt-2 max-w-xl text-sm text-on-surface-variant">
          Quanto severo è il filtro prima che un lead arrivi in outreach.
          Controlla tutto e poi conferma.
        </p>
      </header>

      {/* Threshold buckets */}
      <div className="rounded-xl bg-surface-container-lowest p-6 shadow-ambient-sm">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Soglia scoring
        </p>
        <p className="mt-1 text-sm text-on-surface-variant">
          Solo i lead con punteggio ≥ soglia entrano nel funnel di outreach.
        </p>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {THRESHOLD_BUCKETS.map((b) => {
            const active = form.scoring_threshold === b.value;
            return (
              <button
                key={b.value}
                type="button"
                onClick={() => onChange({ ...form, scoring_threshold: b.value })}
                className={cn(
                  'flex flex-col items-start gap-2 rounded-xl p-4 text-left transition-all',
                  active
                    ? 'bg-gradient-primary text-on-primary shadow-ambient ring-2 ring-primary'
                    : 'bg-surface-container-low text-on-surface hover:bg-surface-container',
                )}
              >
                <div className="flex w-full items-baseline justify-between">
                  <span className="text-sm font-semibold">{b.label}</span>
                  <span
                    className={cn(
                      'font-headline text-2xl font-bold tabular-nums tracking-tighter',
                      active ? 'text-on-primary' : 'text-primary',
                    )}
                  >
                    {b.value}
                  </span>
                </div>
                <span
                  className={cn(
                    'text-xs',
                    active ? 'text-on-primary/85' : 'text-on-surface-variant',
                  )}
                >
                  {b.desc}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Review */}
      <ReviewPanel form={form} />
    </section>
  );
}

// ---------------------------------------------------------------------------

function ReviewPanel({ form }: { form: WizardForm }) {
  const rows: { label: string; value: React.ReactNode }[] = [
    {
      label: 'Modalità',
      value: SCAN_MODE_LABELS[form.scan_mode],
    },
    {
      label: 'Segmenti',
      value: form.target_segments
        .map((s) => (s === 'b2b' ? 'B2B' : 'B2C'))
        .join(' + '),
    },
    {
      label: 'Codici ATECO',
      value:
        form.ateco_codes.length === 0
          ? 'Nessuno (scan opportunistico)'
          : `${form.ateco_codes.length} selezionati`,
    },
    {
      label: 'kWp minimo B2B',
      value: form.min_kwp_b2b != null ? `${form.min_kwp_b2b} kWp` : '—',
    },
    {
      label: 'kWp minimo B2C',
      value: form.min_kwp_b2c != null ? `${form.min_kwp_b2c} kWp` : '—',
    },
    {
      label: 'Ombreggiamento massimo',
      value: formatPercent(form.max_shading),
    },
    {
      label: 'Esposizione minima',
      value: formatPercent(form.min_exposure_score),
    },
    {
      label: 'Zone prioritarie',
      value: form.scan_priority_zones
        .map((z) => ZONE_LABELS[z] ?? z)
        .join(', '),
    },
    {
      label: 'Budget scansione / mese',
      value: formatEur(form.monthly_scan_budget_eur),
    },
    {
      label: 'Budget outreach / mese',
      value: formatEur(form.monthly_outreach_budget_eur),
    },
    {
      label: 'Soglia scoring',
      value: (
        <span className="font-headline text-base font-bold tabular-nums text-primary">
          ≥ {form.scoring_threshold}
        </span>
      ),
    },
  ];

  return (
    <div className="rounded-xl bg-surface-container-lowest p-6 shadow-ambient-sm">
      <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
        Riepilogo configurazione
      </p>
      <p className="mt-1 text-sm text-on-surface-variant">
        Potrai modificare ogni voce più tardi dalla pagina impostazioni.
      </p>
      <dl className="mt-4 divide-y divide-outline-variant/30">
        {rows.map((r) => (
          <div
            key={r.label}
            className="flex items-center justify-between gap-4 py-2.5"
          >
            <dt className="text-xs font-medium uppercase tracking-wide text-on-surface-variant">
              {r.label}
            </dt>
            <dd className="text-right text-sm font-medium text-on-surface">
              {r.value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
