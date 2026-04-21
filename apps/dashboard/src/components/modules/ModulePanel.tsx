'use client';

/**
 * Generic shell for editing a single module.
 *
 * Consumed by:
 *   - the onboarding wizard (one step = one panel in a flow)
 *   - the `/settings/modules/[key]` standalone edit page
 *
 * Responsibilities:
 *   - dispatch the correct form component by `module.module_key`
 *   - buffer unsaved edits locally (so abandoning the form
 *     doesn't mutate the persisted row)
 *   - surface a Save/Cancel row with loading + error states
 *   - toggle the module's `active` flag
 *
 * Stays stateless about navigation — the caller decides where
 * "Save" goes (next step in wizard, or just show a toast).
 */

import { useState, useTransition } from 'react';

import { ApiError } from '@/lib/api-client';
import { upsertModule } from '@/lib/data/modules';
import { cn } from '@/lib/utils';

import type {
  CRMConfig,
  EconomicoConfig,
  ModuleKey,
  OutreachConfig,
  SorgenteConfig,
  TecnicoConfig,
  TenantModule,
} from '@/types/modules';
import {
  MODULE_DESCRIPTIONS,
  MODULE_LABELS,
  withModuleDefaults,
} from '@/types/modules';

import { ModuleCRM } from './module-crm';
import { ModuleEconomico } from './module-economico';
import { ModuleOutreach } from './module-outreach';
import { ModuleSorgente } from './module-sorgente';
import { ModuleTecnico } from './module-tecnico';

export interface ModulePanelProps {
  module: TenantModule;
  onSaved?: (m: TenantModule) => void;
  ctaLabel?: string;
}

export function ModulePanel({
  module,
  onSaved,
  ctaLabel = 'Salva modulo',
}: ModulePanelProps) {
  // Hydrate with per-module defaults so forms never see `undefined`
  // arrays / nested objects (brand-new tenant → DB row with `config: {}`
  // would otherwise crash at e.g. `value.ateco_codes.join(', ')`).
  const [config, setConfig] = useState<unknown>(() =>
    withModuleDefaults(
      module.module_key,
      module.config as Parameters<typeof withModuleDefaults>[1],
    ),
  );
  const [active, setActive] = useState(module.active);
  const [isPending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function handleSave() {
    setError(null);
    startTransition(async () => {
      try {
        const saved = await upsertModule(module.module_key, {
          // biome-ignore lint/suspicious/noExplicitAny: backend validates
          config: config as any,
          active,
        });
        onSaved?.(saved);
      } catch (e) {
        if (e instanceof ApiError) {
          const body = e.body as { detail?: unknown } | undefined;
          setError(
            typeof body?.detail === 'string'
              ? body.detail
              : `Errore ${e.status}: controlla i campi.`,
          );
        } else {
          setError(e instanceof Error ? e.message : 'Errore sconosciuto');
        }
      }
    });
  }

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <h2 className="font-headline text-2xl font-bold tracking-tight text-on-surface">
          {MODULE_LABELS[module.module_key]}
        </h2>
        <p className="text-sm text-on-surface-variant">
          {MODULE_DESCRIPTIONS[module.module_key]}
        </p>
      </header>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={active}
          onChange={(e) => setActive(e.target.checked)}
          className="h-4 w-4 accent-primary"
        />
        <span className="text-on-surface">Modulo attivo</span>
      </label>

      <FormForKey
        moduleKey={module.module_key}
        config={config}
        onChange={setConfig}
      />

      {error && (
        <div
          role="alert"
          className="rounded-lg bg-error-container px-4 py-3 text-sm text-on-error-container"
        >
          {error}
        </div>
      )}

      <div className="flex justify-end">
        <button
          type="button"
          onClick={handleSave}
          disabled={isPending}
          className={cn(
            'rounded-xl bg-primary px-5 py-2 text-sm font-semibold text-on-primary shadow-ambient-sm transition-opacity',
            isPending && 'opacity-60',
          )}
        >
          {isPending ? 'Salvataggio…' : ctaLabel}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dispatcher — keeps the Panel generic while typed forms stay specific.
// ---------------------------------------------------------------------------

function FormForKey({
  moduleKey,
  config,
  onChange,
}: {
  moduleKey: ModuleKey;
  config: unknown;
  onChange: (v: unknown) => void;
}) {
  switch (moduleKey) {
    case 'sorgente':
      return (
        <ModuleSorgente
          value={config as SorgenteConfig}
          onChange={(v) => onChange(v)}
        />
      );
    case 'tecnico':
      return (
        <ModuleTecnico
          value={config as TecnicoConfig}
          onChange={(v) => onChange(v)}
        />
      );
    case 'economico':
      return (
        <ModuleEconomico
          value={config as EconomicoConfig}
          onChange={(v) => onChange(v)}
        />
      );
    case 'outreach':
      return (
        <ModuleOutreach
          value={config as OutreachConfig}
          onChange={(v) => onChange(v)}
        />
      );
    case 'crm':
      return (
        <ModuleCRM
          value={config as CRMConfig}
          onChange={(v) => onChange(v)}
        />
      );
  }
}
