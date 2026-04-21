'use client';

/**
 * Module `sorgente` — discovery source form.
 *
 * B2B fields (ATECO + size + geography) sit above B2C fields (income +
 * household mix). We don't hide one half based on scan_mode because
 * the installer may plan multiple scans with different modes, and
 * editing this module shouldn't require context the form doesn't have.
 *
 * Validation is deferred to the backend (pydantic). This form just
 * bounds numeric inputs and strips whitespace.
 */

import type { SorgenteConfig } from '@/types/modules';

import {
  FieldCard,
  NumberField,
  TagInput,
} from './module-primitives';

export interface ModuleSorgenteProps {
  value: SorgenteConfig;
  onChange: (v: SorgenteConfig) => void;
}

export function ModuleSorgente({ value, onChange }: ModuleSorgenteProps) {
  function set<K extends keyof SorgenteConfig>(
    key: K,
    v: SorgenteConfig[K],
  ) {
    onChange({ ...value, [key]: v });
  }

  return (
    <div className="space-y-4">
      <FieldCard
        title="B2B — Anagrafica target"
        hint="Criteri Atoka applicati al primo livello del funnel."
      >
        <TagInput
          label="Codici ATECO"
          value={value.ateco_codes}
          onChange={(v) => set('ateco_codes', v)}
          placeholder="10.51, 20.11, 25.11"
        />
        <div className="grid grid-cols-2 gap-3">
          <NumberField
            label="Dipendenti min"
            value={value.min_employees}
            onChange={(v) => set('min_employees', v)}
            min={0}
          />
          <NumberField
            label="Dipendenti max"
            value={value.max_employees}
            onChange={(v) => set('max_employees', v)}
            min={0}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <NumberField
            label="Fatturato min"
            value={value.min_revenue_eur}
            onChange={(v) => set('min_revenue_eur', v)}
            suffix="€"
            step={100_000}
          />
          <NumberField
            label="Fatturato max"
            value={value.max_revenue_eur}
            onChange={(v) => set('max_revenue_eur', v)}
            suffix="€"
            step={100_000}
          />
        </div>
      </FieldCard>

      <FieldCard
        title="Geografia"
        hint="Vince il più specifico: CAP > Provincia > Regione."
      >
        <TagInput
          label="Province (sigle)"
          value={value.province}
          onChange={(v) => set('province', v)}
          placeholder="NA, RM, MI"
        />
        <TagInput
          label="Regioni"
          value={value.regioni}
          onChange={(v) => set('regioni', v)}
          placeholder="Campania, Lazio"
        />
        <TagInput
          label="CAP"
          value={value.cap}
          onChange={(v) => set('cap', v)}
          placeholder="80100, 00100"
        />
      </FieldCard>

      <FieldCard
        title="B2C — Fascia reddito CAP"
        hint="Usato solo in modalità b2c_residential (ISTAT). Ignorato per scan B2B."
      >
        <NumberField
          label="Reddito medio min"
          value={value.reddito_min_eur}
          onChange={(v) => set('reddito_min_eur', v ?? 0)}
          suffix="€"
          step={1000}
          min={0}
        />
        <NumberField
          label="% case unifamiliari min"
          value={value.case_unifamiliari_pct_min}
          onChange={(v) => set('case_unifamiliari_pct_min', v ?? 0)}
          suffix="%"
          min={0}
          max={100}
        />
      </FieldCard>
    </div>
  );
}
