'use client';

/**
 * Module `tecnico` — roof qualification thresholds + Solar-gate %.
 *
 * `solar_gate_pct` is the biggest cost dial in the funnel (more = more
 * €€€ to Google Solar). The UI uses a percent slider with a note that
 * makes the trade-off visible.
 */

import type { Orientamento, TecnicoConfig } from '@/types/modules';

import {
  CheckboxGroup,
  FieldCard,
  NumberField,
  SliderField,
} from './module-primitives';

const ALL_ORIENTAMENTI: readonly Orientamento[] = [
  'N',
  'NE',
  'E',
  'SE',
  'S',
  'SO',
  'O',
  'NO',
] as const;

export interface ModuleTecnicoProps {
  value: TecnicoConfig;
  onChange: (v: TecnicoConfig) => void;
}

export function ModuleTecnico({ value, onChange }: ModuleTecnicoProps) {
  function set<K extends keyof TecnicoConfig>(key: K, v: TecnicoConfig[K]) {
    onChange({ ...value, [key]: v });
  }

  return (
    <div className="space-y-4">
      <FieldCard
        title="Soglie tetto"
        hint="Applicate dopo lo scan Google Solar (L4)."
      >
        <NumberField
          label="kW picco min"
          value={value.min_kwp}
          onChange={(v) => set('min_kwp', v ?? 0)}
          suffix="kWp"
          min={0}
          step={5}
        />
        <NumberField
          label="Superficie min"
          value={value.min_area_sqm}
          onChange={(v) => set('min_area_sqm', v ?? 0)}
          suffix="m²"
          min={0}
          step={10}
        />
        <SliderField
          label="Ombreggiamento max"
          value={value.max_shading}
          onChange={(v) => set('max_shading', v)}
          min={0}
          max={1}
        />
        <SliderField
          label="Score esposizione min"
          value={value.min_exposure_score}
          onChange={(v) => set('min_exposure_score', v)}
          min={0}
          max={1}
        />
      </FieldCard>

      <FieldCard title="Orientamento accettato">
        <CheckboxGroup<Orientamento>
          label="Direzioni"
          options={ALL_ORIENTAMENTI}
          value={value.orientamenti_ok}
          onChange={(v) => set('orientamenti_ok', v)}
        />
      </FieldCard>

      <FieldCard
        title="Solar gate (L4)"
        hint="Percentuale di candidati L3 che entrano in Google Solar. Più alto = più costo API."
      >
        <SliderField
          label="Solar gate %"
          value={value.solar_gate_pct}
          onChange={(v) => set('solar_gate_pct', v)}
          min={0.05}
          max={1}
          step={0.05}
        />
        <NumberField
          label="Min candidati sempre ammessi"
          value={value.solar_gate_min_candidates}
          onChange={(v) => set('solar_gate_min_candidates', v ?? 0)}
          min={1}
          max={500}
        />
      </FieldCard>
    </div>
  );
}
