'use client';

/**
 * ModularWizardShell — sequential onboarding across the 5 modules.
 *
 * Each step wraps one `ModulePanel`. The shell advances on save
 * (hence the "Salva e continua" CTA) and lets the installer skip
 * a module (leaves it inactive, moves on).
 *
 * The initial `modules` list comes from `GET /v1/modules` on the
 * server; if the backend synthesises defaults for missing rows the
 * wizard still renders 5 steps uniformly.
 *
 * On final save the shell calls `onComplete` (default: redirect to
 * `/`). The parent `(onboarding)` layout's wizard-pending guard
 * bounces the user back out once all modules are persisted.
 */

import { useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';

import { cn } from '@/lib/utils';

import type { ModuleKey, TenantModule } from '@/types/modules';
import { MODULE_KEYS, MODULE_LABELS } from '@/types/modules';

import { ModulePanel } from './ModulePanel';

export interface ModularWizardShellProps {
  modules: TenantModule[];
  onComplete?: () => void;
}

export function ModularWizardShell({
  modules,
  onComplete,
}: ModularWizardShellProps) {
  const router = useRouter();

  // Normalise the incoming list to always have 5 entries keyed by
  // MODULE_KEYS order. If the backend drops one, it'll be synthesised
  // with defaults by `listModules` on the API — but guard anyway.
  const byKey = useMemo(() => {
    const map = new Map<ModuleKey, TenantModule>();
    for (const m of modules) map.set(m.module_key, m);
    return map;
  }, [modules]);

  const [state, setState] = useState(byKey);
  const [stepIdx, setStepIdx] = useState(0);

  const currentKey = MODULE_KEYS[stepIdx] as ModuleKey;
  const currentModule = state.get(currentKey);
  const isLast = stepIdx === MODULE_KEYS.length - 1;

  function handleSaved(m: TenantModule) {
    setState((prev) => {
      const next = new Map(prev);
      next.set(m.module_key, m);
      return next;
    });
    if (isLast) {
      if (onComplete) onComplete();
      else {
        router.refresh();
        router.push('/');
      }
      return;
    }
    setStepIdx((i) => i + 1);
  }

  function handleSkip() {
    if (isLast) {
      if (onComplete) onComplete();
      else router.push('/');
      return;
    }
    setStepIdx((i) => i + 1);
  }

  if (!currentModule) return null;

  return (
    <div className="space-y-8">
      <StepIndicator stepIdx={stepIdx} />

      {/*
        `key` forces React to discard and remount ModulePanel whenever
        the step changes. Without it, the inner `useState(module.config)`
        stays frozen on the *first* step's config while `module_key`
        flips — so the dispatcher renders the new module's form
        against the previous module's shape (e.g. a Tecnico form reading
        `value.orientamenti_ok` out of a Sorgente-shaped config), which
        hits `undefined.includes()` on the first render of step 2.
        Each step is semantically a distinct form — no shared state.
      */}
      <ModulePanel
        key={currentKey}
        module={currentModule}
        onSaved={handleSaved}
        ctaLabel={isLast ? 'Salva e completa' : 'Salva e continua'}
      />

      <div className="flex items-center justify-between border-t border-outline-variant/30 pt-4 text-sm">
        <button
          type="button"
          onClick={() => setStepIdx((i) => Math.max(0, i - 1))}
          disabled={stepIdx === 0}
          className="text-on-surface-variant disabled:opacity-40"
        >
          ← Indietro
        </button>
        <button
          type="button"
          onClick={handleSkip}
          className="text-on-surface-variant underline"
        >
          {isLast ? 'Salta e termina' : 'Salta questo modulo'}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function StepIndicator({ stepIdx }: { stepIdx: number }) {
  const total = MODULE_KEYS.length;
  const pct = ((stepIdx + 1) / total) * 100;
  return (
    <div>
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Wizard modulare SolarLead
        </p>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-primary tabular-nums">
          {stepIdx + 1} / {total} · {MODULE_LABELS[MODULE_KEYS[stepIdx] as ModuleKey]}
        </p>
      </div>
      <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-surface-container-high">
        <div
          className="h-full rounded-full bg-primary transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
      <ul className="mt-3 flex items-center justify-between text-[10px] font-medium uppercase tracking-widest text-on-surface-variant">
        {MODULE_KEYS.map((k, i) => (
          <li
            key={k}
            className={cn(
              'transition-colors',
              i === stepIdx && 'text-primary',
              i < stepIdx && 'text-on-surface',
            )}
          >
            {MODULE_LABELS[k]}
          </li>
        ))}
      </ul>
    </div>
  );
}
