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
 *
 * Sprint C.2 — "Settori target" multi-select. Loaded from
 * `GET /v1/sectors/wizard-groups`. Selecting a sector palette
 * suggests its ATECO codes; the operator can still refine the
 * `ateco_codes` list manually.
 */

import { useEffect, useMemo, useState } from 'react';

import { listWizardGroups } from '@/lib/data/sectors';
import { cn } from '@/lib/utils';
import type { SorgenteConfig, WizardGroupOption } from '@/types/modules';

import {
  FieldCard,
  NumberField,
  TagInput,
} from './module-primitives';

export interface ModuleSorgenteProps {
  value: SorgenteConfig;
  onChange: (v: SorgenteConfig) => void;
  /**
   * When true, the three geographic fields (province / regioni / cap)
   * are rendered as read-only. Non-geo fields (ATECO, employees,
   * revenue, B2C income bands) stay editable. Set to true once the
   * tenant has confirmed their territorial exclusivity at the end of
   * onboarding (`tenants.territory_locked_at IS NOT NULL`).
   */
  geoLocked?: boolean;
}

export function ModuleSorgente({
  value,
  onChange,
  geoLocked = false,
}: ModuleSorgenteProps) {
  function set<K extends keyof SorgenteConfig>(
    key: K,
    v: SorgenteConfig[K],
  ) {
    onChange({ ...value, [key]: v });
  }

  // -------------------------------------------------------------------
  // Sector palette catalog — fetched once when the form mounts.
  // Stable reference data (ateco_google_types is seeded by migration)
  // so we don't refetch on every keystroke.
  // -------------------------------------------------------------------
  const [wizardGroups, setWizardGroups] = useState<WizardGroupOption[]>([]);
  const [groupsError, setGroupsError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listWizardGroups()
      .then((rows) => {
        if (!cancelled) setWizardGroups(rows);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : 'load_failed';
          setGroupsError(msg);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Lookup helper for the currently selected groups → display info
  // (used in the "selezionati" preview line).
  const selectedGroupLabels = useMemo(() => {
    const byCode = new Map(wizardGroups.map((g) => [g.wizard_group, g]));
    return value.target_wizard_groups
      .map((code) => byCode.get(code)?.display_name ?? code)
      .join(' · ');
  }, [wizardGroups, value.target_wizard_groups]);

  function toggleWizardGroup(code: string) {
    const has = value.target_wizard_groups.includes(code);
    onChange({
      ...value,
      target_wizard_groups: has
        ? value.target_wizard_groups.filter((c) => c !== code)
        : [...value.target_wizard_groups, code],
    });
  }

  return (
    <div className="space-y-4">
      <FieldCard
        title="Settori target"
        hint="Seleziona uno o più settori. Il funnel adatta automaticamente keyword di discovery, scoring e mapping ATECO. Lascia vuoto per la modalità classica (solo codici ATECO manuali)."
      >
        {groupsError ? (
          <p className="text-xs text-error">
            Impossibile caricare i settori — modifica solo i codici ATECO.
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {wizardGroups.map((g) => {
            const active = value.target_wizard_groups.includes(g.wizard_group);
            const tooltip = [
              g.description,
              g.ateco_examples.length > 0
                ? `Esempi: ${g.ateco_examples.join('; ')}`
                : null,
              g.typical_kwp_range_min !== null && g.typical_kwp_range_max !== null
                ? `kWp tipici: ${g.typical_kwp_range_min}–${g.typical_kwp_range_max}`
                : null,
            ]
              .filter(Boolean)
              .join('\n');
            return (
              <button
                key={g.wizard_group}
                type="button"
                onClick={() => toggleWizardGroup(g.wizard_group)}
                title={tooltip}
                className={cn(
                  'rounded-full px-3 py-1.5 text-xs font-semibold transition-colors',
                  active
                    ? 'bg-primary text-on-primary shadow-ambient-sm'
                    : 'bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest',
                )}
              >
                {g.display_name}
              </button>
            );
          })}
        </div>
        {value.target_wizard_groups.length > 0 ? (
          <p className="mt-1 text-xs text-on-surface-variant">
            Selezionati: {selectedGroupLabels}
          </p>
        ) : null}
      </FieldCard>

      <FieldCard
        title="B2B — Anagrafica target"
        hint="Criteri di scoperta aziende applicati al primo livello del funnel. Quando hai selezionato i settori sopra, lasciare i codici ATECO vuoti li deriva automaticamente."
      >
        <TagInput
          label="Codici ATECO (opzionale se settori target selezionati)"
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
        hint={
          geoLocked
            ? 'Zona di esclusiva confermata — contatta il supporto per modifiche.'
            : 'Vince il più specifico: CAP > Provincia > Regione.'
        }
      >
        <TagInput
          label="Province (sigle)"
          value={value.province}
          onChange={(v) => set('province', v)}
          placeholder="NA, RM, MI"
          readOnly={geoLocked}
        />
        <TagInput
          label="Regioni"
          value={value.regioni}
          onChange={(v) => set('regioni', v)}
          placeholder="Campania, Lazio"
          readOnly={geoLocked}
        />
        <TagInput
          label="CAP"
          value={value.cap}
          onChange={(v) => set('cap', v)}
          placeholder="80100, 00100"
          readOnly={geoLocked}
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
