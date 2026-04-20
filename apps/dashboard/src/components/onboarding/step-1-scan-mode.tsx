'use client';

/**
 * Step 1 — Scan mode + segment.
 *
 * Three big selectable cards drive the entire downstream scan
 * strategy. Each card highlights cost-per-month + conversion rate so
 * the user's tradeoff is visible upfront.
 */

import { cn } from '@/lib/utils';
import type { ScanMode, TargetSegment } from '@/types/db';

import type { WizardForm } from './wizard-types';

interface ModeOption {
  value: ScanMode;
  title: string;
  badge: string;
  cost: string;
  description: string;
  bullet_1: string;
  bullet_2: string;
}

const MODES: ModeOption[] = [
  {
    value: 'b2b_precision',
    title: 'B2B Precision',
    badge: 'Consigliato',
    cost: '~200 €/mese',
    description:
      'Parte dalle aziende reali (Google Places) e scansiona solo i tetti che corrispondono ai tuoi settori target.',
    bullet_1: 'Cost-per-lead basso',
    bullet_2: 'Altissimo signal-to-noise',
  },
  {
    value: 'opportunistic',
    title: 'Opportunistic',
    badge: 'Mix B2B + B2C',
    cost: '~1.500 €/mese',
    description:
      'Scansiona griglia territoriale completa; classifica B2B/B2C via visione e applica filtri standard.',
    bullet_1: 'Flusso lead bilanciato',
    bullet_2: 'Default storico della piattaforma',
  },
  {
    value: 'volume',
    title: 'Volume Play',
    badge: 'Residenziale',
    cost: '~3.000 €/mese',
    description:
      'Griglia densa residenziale con soglie permissive. Pensato per campagne postali di massa.',
    bullet_1: 'Copertura massima del territorio',
    bullet_2: 'Ottimale con contratti volumetrici',
  },
];

export interface Step1Props {
  form: WizardForm;
  onChange: (f: WizardForm) => void;
}

export function Step1ScanMode({ form, onChange }: Step1Props) {
  function selectMode(mode: ScanMode) {
    // Segment defaults follow mode to reduce decision fatigue.
    const segments: TargetSegment[] =
      mode === 'volume' ? ['b2c'] : mode === 'b2b_precision' ? ['b2b'] : ['b2b', 'b2c'];
    onChange({ ...form, scan_mode: mode, target_segments: segments });
  }

  function toggleSegment(seg: TargetSegment) {
    const has = form.target_segments.includes(seg);
    const next = has
      ? form.target_segments.filter((s) => s !== seg)
      : [...form.target_segments, seg];
    // Enforce at least one; if last removed, ignore the click.
    if (next.length === 0) return;
    onChange({ ...form, target_segments: next });
  }

  return (
    <section className="space-y-8">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Passo 1 di 5
        </p>
        <h2 className="mt-1 font-headline text-3xl font-bold tracking-tighter">
          Come cerchiamo i lead?
        </h2>
        <p className="mt-2 max-w-xl text-sm text-on-surface-variant">
          Scegli la strategia di scansione. Potrai cambiarla dalla pagina
          Impostazioni in qualsiasi momento.
        </p>
      </header>

      {/* Mode cards */}
      <div className="grid gap-4 md:grid-cols-3">
        {MODES.map((m) => {
          const active = form.scan_mode === m.value;
          return (
            <button
              key={m.value}
              type="button"
              onClick={() => selectMode(m.value)}
              className={cn(
                'group relative flex flex-col gap-3 rounded-xl p-6 text-left transition-all',
                active
                  ? 'bg-gradient-primary text-on-primary shadow-ambient ring-2 ring-primary'
                  : 'bg-surface-container-lowest text-on-surface shadow-ambient-sm hover:shadow-ambient hover:-translate-y-0.5',
              )}
            >
              <span
                className={cn(
                  'self-start rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest',
                  active
                    ? 'bg-white/20 text-on-primary'
                    : 'bg-primary-container text-on-primary-container',
                )}
              >
                {m.badge}
              </span>
              <h3 className="font-headline text-xl font-bold tracking-tighter">
                {m.title}
              </h3>
              <p
                className={cn(
                  'text-sm',
                  active ? 'text-on-primary/90' : 'text-on-surface-variant',
                )}
              >
                {m.description}
              </p>
              <dl className="mt-auto space-y-1 pt-2 text-xs">
                <div className="flex justify-between">
                  <dt className={active ? 'text-on-primary/80' : 'text-on-surface-variant'}>
                    Costo stimato
                  </dt>
                  <dd className="font-semibold">{m.cost}</dd>
                </div>
                <p className={active ? 'text-on-primary/80' : 'text-on-surface-variant'}>
                  ✓ {m.bullet_1}
                </p>
                <p className={active ? 'text-on-primary/80' : 'text-on-surface-variant'}>
                  ✓ {m.bullet_2}
                </p>
              </dl>
            </button>
          );
        })}
      </div>

      {/* Segment toggle */}
      <div className="rounded-xl bg-surface-container-lowest p-6 shadow-ambient-sm">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Segmenti target
        </p>
        <p className="mt-1 text-sm text-on-surface-variant">
          Quali clienti contatti? Almeno uno deve essere attivo.
        </p>
        <div className="mt-4 flex gap-3">
          {(['b2b', 'b2c'] as TargetSegment[]).map((seg) => {
            const active = form.target_segments.includes(seg);
            return (
              <button
                key={seg}
                type="button"
                onClick={() => toggleSegment(seg)}
                className={cn(
                  'rounded-full px-5 py-2 text-sm font-semibold transition-colors',
                  active
                    ? 'bg-primary text-on-primary'
                    : 'bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest',
                )}
              >
                {seg.toUpperCase()} ·{' '}
                <span className="font-medium opacity-80">
                  {seg === 'b2b' ? 'Aziende' : 'Residenziale'}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
}
