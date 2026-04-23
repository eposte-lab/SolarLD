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
  /**
   * When true, the Sorgente form renders its three geographic fields
   * (province / regioni / cap) as read-only. Passed from `/settings/
   * modules/sorgente` once the tenant has confirmed their territorial
   * exclusivity. Other modules ignore it.
   */
  territoryLocked?: boolean;
}

export function ModulePanel({
  module,
  onSaved,
  ctaLabel = 'Salva modulo',
  territoryLocked = false,
}: ModulePanelProps) {
  // Boundary hydration — one single shot at the edge of the form tree.
  //
  // The server path (`modules.server.ts` for SSR, `hydrate_config` in
  // `tenant_module_service.py` for the API route) already guarantees a
  // complete config matching the schema. We run `withModuleDefaults`
  // once more here because this component is ALSO reachable as a client
  // boundary from stale bundles, browser cache hits, or tenants whose
  // five-row backfill predates migration 0036's trigger — and the
  // contractual cost of an extra shallow merge is trivial compared to
  // the crash class (`undefined.includes()` etc.) it closes.
  //
  // The child form components can therefore legitimately assume
  // `value.<field>` is always present, and we keep the defensive
  // guards OUT of the primitives where they'd be noise per call site.
  const [config, setConfig] = useState<unknown>(() =>
    withModuleDefaults(
      module.module_key,
      // biome-ignore lint/suspicious/noExplicitAny: config shape narrows via module_key
      module.config as any,
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
        territoryLocked={territoryLocked}
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
  territoryLocked = false,
}: {
  moduleKey: ModuleKey;
  config: unknown;
  onChange: (v: unknown) => void;
  territoryLocked?: boolean;
}) {
  switch (moduleKey) {
    case 'sorgente':
      return (
        <ModuleSorgente
          value={config as SorgenteConfig}
          onChange={(v) => onChange(v)}
          geoLocked={territoryLocked}
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
