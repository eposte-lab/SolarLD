'use client';

/**
 * Module `economico` — parametri commerciali.
 *
 * UI surface is intentionally restricted to ticket medio + ROI target.
 * The per-scan / monthly outreach budget caps (`budget_scan_eur`,
 * `budget_outreach_eur_month`) stay in the backend schema as operational
 * safety nets managed by ops — the installer never sees or tweaks them,
 * since they pay a flat monthly fee and per-lead economics are not
 * their concern. The form passes through the persisted values untouched
 * so the backend continues to enforce cost caps with ops-controlled
 * defaults.
 */

import type { EconomicoConfig } from '@/types/modules';

import { FieldCard, NumberField } from './module-primitives';

export interface ModuleEconomicoProps {
  value: EconomicoConfig;
  onChange: (v: EconomicoConfig) => void;
}

export function ModuleEconomico({ value, onChange }: ModuleEconomicoProps) {
  function set<K extends keyof EconomicoConfig>(
    key: K,
    v: EconomicoConfig[K],
  ) {
    onChange({ ...value, [key]: v });
  }

  return (
    <div className="space-y-4">
      <FieldCard
        title="Parametri commerciali"
        hint="Usati per stimare ROI dei tetti e calibrare la narrativa dei preventivi."
      >
        <NumberField
          label="Ticket medio"
          value={value.ticket_medio_eur}
          onChange={(v) => set('ticket_medio_eur', v ?? 0)}
          suffix="€"
          min={0}
          step={500}
        />
        <NumberField
          label="ROI target (payback)"
          value={value.roi_target_years}
          onChange={(v) => set('roi_target_years', v ?? 0)}
          suffix="anni"
          min={1}
          max={30}
        />
      </FieldCard>
    </div>
  );
}
