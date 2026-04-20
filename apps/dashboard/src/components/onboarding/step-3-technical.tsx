'use client';

/**
 * Step 3 — Technical filters.
 *
 * These are the thresholds applied after a Google Solar scan to
 * decide if a roof is worth scoring. The wizard only exposes the
 * three most impactful knobs (min kWp per segment + shading +
 * exposure); `min_area_sqm` stays at schema defaults.
 *
 * Inputs are plain `<input type="range">` — no 3rd-party slider.
 */

import { cn } from '@/lib/utils';

import type { WizardForm } from './wizard-types';

export interface Step3Props {
  form: WizardForm;
  onChange: (f: WizardForm) => void;
}

function PercentLabel({ value }: { value: number }) {
  return (
    <span className="font-headline text-xl font-bold tabular-nums text-primary">
      {Math.round(value * 100)}%
    </span>
  );
}

export function Step3Technical({ form, onChange }: Step3Props) {
  const hasB2B = form.target_segments.includes('b2b');
  const hasB2C = form.target_segments.includes('b2c');

  return (
    <section className="space-y-6">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Passo 3 di 5
        </p>
        <h2 className="mt-1 font-headline text-3xl font-bold tracking-tighter">
          Filtri tecnici
        </h2>
        <p className="mt-2 max-w-xl text-sm text-on-surface-variant">
          Escludi in automatico i tetti che non rientrano nei tuoi standard.
          Valori suggeriti funzionano bene per la maggior parte degli
          installatori italiani.
        </p>
      </header>

      {/* Min kWp — one card per active segment */}
      <div
        className={cn(
          'grid gap-4',
          hasB2B && hasB2C ? 'md:grid-cols-2' : 'md:grid-cols-1',
        )}
      >
        {hasB2B && (
          <ThresholdCard
            label="Potenza minima B2B"
            description="Sotto questa soglia il tetto viene scartato automaticamente."
            valueSuffix="kWp"
            min={0}
            max={500}
            step={5}
            value={form.min_kwp_b2b ?? 0}
            onChange={(v) => onChange({ ...form, min_kwp_b2b: v })}
          />
        )}
        {hasB2C && (
          <ThresholdCard
            label="Potenza minima B2C"
            description="Per l'outreach residenziale il minimo è normalmente molto basso."
            valueSuffix="kWp"
            min={0}
            max={20}
            step={0.5}
            value={form.min_kwp_b2c ?? 0}
            onChange={(v) => onChange({ ...form, min_kwp_b2c: v })}
          />
        )}
      </div>

      {/* Shading + Exposure */}
      <div className="rounded-xl bg-surface-container-lowest p-6 shadow-ambient-sm">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Ombreggiamento & esposizione
        </p>

        <div className="mt-4 space-y-6">
          <div>
            <div className="flex items-end justify-between">
              <label className="text-sm font-semibold">
                Ombreggiamento massimo tollerato
              </label>
              <PercentLabel value={form.max_shading} />
            </div>
            <p className="mt-1 text-xs text-on-surface-variant">
              Oltre questa soglia di ombra (misurata da Google Solar) il tetto
              viene scartato.
            </p>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={form.max_shading}
              onChange={(e) =>
                onChange({ ...form, max_shading: parseFloat(e.target.value) })
              }
              className="mt-3 w-full accent-primary"
            />
            <div className="mt-1 flex justify-between text-[10px] font-medium uppercase tracking-widest text-on-surface-variant">
              <span>Solo tetti liberi</span>
              <span>Anche ombra piena</span>
            </div>
          </div>

          <div>
            <div className="flex items-end justify-between">
              <label className="text-sm font-semibold">
                Score minimo di esposizione
              </label>
              <PercentLabel value={form.min_exposure_score} />
            </div>
            <p className="mt-1 text-xs text-on-surface-variant">
              Quanto vuoi che il tetto sia ben orientato (sud &gt; sud-est &gt;
              est...). Default 60% scarta solo i casi peggiori.
            </p>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={form.min_exposure_score}
              onChange={(e) =>
                onChange({
                  ...form,
                  min_exposure_score: parseFloat(e.target.value),
                })
              }
              className="mt-3 w-full accent-primary"
            />
            <div className="mt-1 flex justify-between text-[10px] font-medium uppercase tracking-widest text-on-surface-variant">
              <span>Qualsiasi orientamento</span>
              <span>Solo sud perfetto</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------

interface ThresholdCardProps {
  label: string;
  description: string;
  valueSuffix: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (v: number) => void;
}

function ThresholdCard({
  label,
  description,
  valueSuffix,
  min,
  max,
  step,
  value,
  onChange,
}: ThresholdCardProps) {
  return (
    <div className="rounded-xl bg-surface-container-lowest p-6 shadow-ambient-sm">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm font-semibold">{label}</p>
          <p className="mt-1 text-xs text-on-surface-variant">{description}</p>
        </div>
        <div className="text-right">
          <span className="font-headline text-3xl font-bold tabular-nums text-primary">
            {value}
          </span>
          <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            {valueSuffix}
          </p>
        </div>
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
        <span>{min}</span>
        <span>{max}</span>
      </div>
    </div>
  );
}
