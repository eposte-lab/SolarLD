'use client';

/**
 * Module `economico` — pricing + budget caps.
 *
 * The key value is `budget_scan_eur`: the orchestrator short-circuits
 * levels past this cap. Installers typically want €5-50/scan for
 * Precision and €1-5/scan for Volume modes.
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
      <FieldCard title="Offerta & ROI">
        <NumberField
          label="Ticket medio"
          value={value.ticket_medio_eur}
          onChange={(v) => set('ticket_medio_eur', v ?? 0)}
          suffix="€"
          min={0}
          step={500}
        />
        <NumberField
          label="ROI target"
          value={value.roi_target_years}
          onChange={(v) => set('roi_target_years', v ?? 0)}
          suffix="anni"
          min={1}
          max={30}
        />
      </FieldCard>

      <FieldCard
        title="Budget"
        hint="Il funnel si ferma quando il costo accumulato supera il cap per scan."
      >
        <NumberField
          label="Budget per scan"
          value={value.budget_scan_eur}
          onChange={(v) => set('budget_scan_eur', v ?? 0)}
          suffix="€"
          min={0}
          step={5}
        />
        <NumberField
          label="Budget outreach mensile"
          value={value.budget_outreach_eur_month}
          onChange={(v) => set('budget_outreach_eur_month', v ?? 0)}
          suffix="€"
          min={0}
          step={50}
        />
      </FieldCard>
    </div>
  );
}
