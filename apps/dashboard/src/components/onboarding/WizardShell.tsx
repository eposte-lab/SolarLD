'use client';

/**
 * WizardShell — orchestrates the 5-step onboarding flow.
 *
 * Owns:
 *   - the canonical `WizardForm` state (lifted here, passed to each step)
 *   - step navigation (Indietro / Avanti / Conferma)
 *   - progress bar
 *   - submit → POST /v1/tenant-config → router.refresh() so the
 *     `(onboarding)` layout's guard bounces back to `/`.
 */

import { useRouter } from 'next/navigation';
import { useState, useTransition } from 'react';

import { GradientButton } from '@/components/ui/gradient-button';
import { cn } from '@/lib/utils';
import type { AtecoOption } from '@/types/db';

import { Step1ScanMode } from './step-1-scan-mode';
import { Step2Ateco } from './step-2-ateco';
import { Step3Technical } from './step-3-technical';
import { Step4Territory } from './step-4-territory';
import { Step5Scoring } from './step-5-scoring';
import { Step6Integrations } from './step-6-integrations';
import {
  canAdvance,
  defaultForm,
  type StepId,
  type WizardForm,
} from './wizard-types';
import { submitWizard } from './wizard-submit';

export interface WizardShellProps {
  options: AtecoOption[];
}

const STEP_LABELS: Record<StepId, string> = {
  1: 'Modalità',
  2: 'Settori',
  3: 'Tecnica',
  4: 'Territorio',
  5: 'Scoring',
  6: 'Integrazioni',
};

const TOTAL_STEPS = 6;

export function WizardShell({ options }: WizardShellProps) {
  const router = useRouter();
  const [step, setStep] = useState<StepId>(1);
  const [form, setForm] = useState<WizardForm>(defaultForm);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const isLast = step === TOTAL_STEPS;
  const canGoNext = canAdvance(step, form);

  function goBack() {
    setError(null);
    if (step > 1) setStep((s) => (s - 1) as StepId);
  }

  function goNext() {
    setError(null);
    if (!canGoNext) return;
    if (!isLast) setStep((s) => (s + 1) as StepId);
  }

  function handleSubmit() {
    setError(null);
    startTransition(async () => {
      const res = await submitWizard(form);
      if (!res.ok) {
        setError(res.message);
        return;
      }
      // Layout guard will redirect back to `/` on next render.
      router.refresh();
      router.push('/');
    });
  }

  return (
    <div className="space-y-8">
      <Header step={step} />

      <div className="min-h-[420px]">
        {step === 1 && <Step1ScanMode form={form} onChange={setForm} />}
        {step === 2 && (
          <Step2Ateco form={form} onChange={setForm} options={options} />
        )}
        {step === 3 && <Step3Technical form={form} onChange={setForm} />}
        {step === 4 && <Step4Territory form={form} onChange={setForm} />}
        {step === 5 && <Step5Scoring form={form} onChange={setForm} />}
        {step === 6 && <Step6Integrations form={form} onChange={setForm} />}
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-lg bg-error-container px-4 py-3 text-sm font-medium text-on-error-container shadow-ambient-sm"
        >
          {error}
        </div>
      )}

      <NavBar
        step={step}
        isLast={isLast}
        canGoNext={canGoNext}
        isPending={isPending}
        onBack={goBack}
        onNext={goNext}
        onSubmit={handleSubmit}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------

function Header({ step }: { step: StepId }) {
  const pct = (step / TOTAL_STEPS) * 100;
  return (
    <div>
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Configurazione SolarLead
        </p>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-primary tabular-nums">
          {step} / {TOTAL_STEPS} · {STEP_LABELS[step]}
        </p>
      </div>
      <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-surface-container-high">
        <div
          className="h-full rounded-full bg-gradient-primary transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
      <ul className="mt-3 flex items-center justify-between text-[10px] font-medium uppercase tracking-widest text-on-surface-variant">
        {([1, 2, 3, 4, 5, 6] as StepId[]).map((s) => (
          <li
            key={s}
            className={cn(
              'transition-colors',
              s === step && 'text-primary',
              s < step && 'text-on-surface',
            )}
          >
            {STEP_LABELS[s]}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------

interface NavBarProps {
  step: StepId;
  isLast: boolean;
  canGoNext: boolean;
  isPending: boolean;
  onBack: () => void;
  onNext: () => void;
  onSubmit: () => void;
}

function NavBar({
  step,
  isLast,
  canGoNext,
  isPending,
  onBack,
  onNext,
  onSubmit,
}: NavBarProps) {
  return (
    <div className="flex items-center justify-between border-t border-outline-variant/30 pt-6">
      <GradientButton
        variant="ghost"
        size="md"
        onClick={onBack}
        disabled={step === 1 || isPending}
      >
        ← Indietro
      </GradientButton>

      {isLast ? (
        <GradientButton
          variant="primary"
          size="lg"
          onClick={onSubmit}
          disabled={!canGoNext || isPending}
        >
          {isPending ? 'Salvataggio…' : 'Conferma configurazione'}
        </GradientButton>
      ) : (
        <GradientButton
          variant="primary"
          size="md"
          onClick={onNext}
          disabled={!canGoNext}
        >
          Avanti →
        </GradientButton>
      )}
    </div>
  );
}
