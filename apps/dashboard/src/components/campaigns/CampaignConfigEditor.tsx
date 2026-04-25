'use client';

/**
 * CampaignConfigEditor — edit the 5 module configs for an acquisition campaign.
 *
 * Unlike ModulePanel (which calls PATCH /v1/modules/:key → tenant_modules),
 * this component calls PATCH /v1/acquisition-campaigns/:id so the save
 * is scoped to the campaign snapshot, not the tenant's global module config.
 *
 * The tab bar lets the operator jump to any of the 5 modules without
 * committing; "Salva" saves only the currently visible module config block.
 */

import { useState, useTransition } from 'react';

import { ApiError } from '@/lib/api-client';
import { patchAcquisitionCampaign } from '@/lib/data/campaign-overrides';
import { cn } from '@/lib/utils';
import type {
  CRMConfig,
  EconomicoConfig,
  ModuleKey,
  OutreachConfig,
  SorgenteConfig,
  TecnicoConfig,
} from '@/types/modules';
import {
  MODULE_LABELS,
  withModuleDefaults,
} from '@/types/modules';
import type { AcquisitionCampaignRow } from '@/types/db';

import { ModuleCRM } from '@/components/modules/module-crm';
import { ModuleEconomico } from '@/components/modules/module-economico';
import { ModuleOutreach } from '@/components/modules/module-outreach';
import { ModuleSorgente } from '@/components/modules/module-sorgente';
import { ModuleTecnico } from '@/components/modules/module-tecnico';

const MODULE_KEYS: readonly ModuleKey[] = [
  'sorgente',
  'tecnico',
  'economico',
  'outreach',
  'crm',
] as const;

const CONFIG_FIELD: Record<ModuleKey, keyof AcquisitionCampaignRow> = {
  sorgente: 'sorgente_config',
  tecnico: 'tecnico_config',
  economico: 'economico_config',
  outreach: 'outreach_config',
  crm: 'crm_config',
};

interface Props {
  campaign: AcquisitionCampaignRow;
  territoryLocked?: boolean;
  onSaved?: (updatedConfig: Record<string, unknown>, key: ModuleKey) => void;
}

export function CampaignConfigEditor({
  campaign,
  territoryLocked = false,
  onSaved,
}: Props) {
  const [activeKey, setActiveKey] = useState<ModuleKey>('sorgente');

  // Local state: one buffer per module key, initialised from campaign props.
  // biome-ignore lint/suspicious/noExplicitAny: campaign configs are typed by module_key at runtime
  const [configs, setConfigs] = useState<Record<ModuleKey, unknown>>(() => ({
    sorgente: withModuleDefaults('sorgente', campaign.sorgente_config as any),
    tecnico: withModuleDefaults('tecnico', campaign.tecnico_config as any),
    economico: withModuleDefaults('economico', campaign.economico_config as any),
    outreach: withModuleDefaults('outreach', campaign.outreach_config as any),
    crm: withModuleDefaults('crm', campaign.crm_config as any),
  }));

  const [isPending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [savedKey, setSavedKey] = useState<ModuleKey | null>(null);

  function handleChange(key: ModuleKey, value: unknown) {
    setConfigs((prev) => ({ ...prev, [key]: value }));
  }

  function handleSave() {
    setError(null);
    const key = activeKey;
    const fieldName = CONFIG_FIELD[key];
    const value = configs[key];

    startTransition(async () => {
      try {
        await patchAcquisitionCampaign(campaign.id, { [fieldName]: value });
        setSavedKey(key);
        setTimeout(() => setSavedKey(null), 2500);
        onSaved?.(value as Record<string, unknown>, key);
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
    <div className="space-y-4">
      {/* Tab bar */}
      <div className="flex gap-1 rounded-xl bg-surface-container-low p-1">
        {MODULE_KEYS.map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => setActiveKey(key)}
            className={cn(
              'flex-1 rounded-lg px-2 py-1.5 text-xs font-semibold transition-colors',
              activeKey === key
                ? 'bg-surface text-on-surface shadow-ambient-sm'
                : 'text-on-surface-variant hover:bg-surface-container-high',
            )}
          >
            {MODULE_LABELS[key]}
          </button>
        ))}
      </div>

      {/* Active form */}
      <div className="rounded-xl border border-outline-variant/40 bg-surface-container-lowest p-4">
        <ModuleFormForKey
          moduleKey={activeKey}
          config={configs[activeKey]}
          onChange={(v) => handleChange(activeKey, v)}
          territoryLocked={territoryLocked}
        />
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-lg bg-error-container px-4 py-3 text-sm text-on-error-container"
        >
          {error}
        </div>
      )}

      {savedKey && (
        <p className="text-sm font-semibold text-primary">
          ✓ {MODULE_LABELS[savedKey]} salvato nella campagna.
        </p>
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
          {isPending ? 'Salvataggio…' : `Salva ${MODULE_LABELS[activeKey]}`}
        </button>
      </div>
    </div>
  );
}

function ModuleFormForKey({
  moduleKey,
  config,
  onChange,
  territoryLocked,
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
          onChange={onChange}
          geoLocked={territoryLocked}
        />
      );
    case 'tecnico':
      return <ModuleTecnico value={config as TecnicoConfig} onChange={onChange} />;
    case 'economico':
      return <ModuleEconomico value={config as EconomicoConfig} onChange={onChange} />;
    case 'outreach':
      return <ModuleOutreach value={config as OutreachConfig} onChange={onChange} />;
    case 'crm':
      return <ModuleCRM value={config as CRMConfig} onChange={onChange} />;
  }
}
