'use client';

/**
 * Territory config panel — edit wizard_groups + province directly
 * from the /territorio page without going through onboarding.
 *
 * Reads the current Sorgente module config on mount, lets the operator
 * toggle sectors and add/remove provinces, then saves via
 * PUT /v1/modules/sorgente. After save the "Rimappa" / "Avvia scansione"
 * buttons will use the new values.
 */

import { useEffect, useState } from 'react';

import { getModule, upsertModule } from '@/lib/data/modules';
import { DEFAULT_SORGENTE } from '@/types/modules';

// Static list — mirrors the 0098 seed. Fetching from /v1/sectors/wizard-groups
// at runtime is also fine but this avoids an extra round-trip.
const WIZARD_GROUPS: { value: string; label: string }[] = [
  { value: 'industry_heavy', label: 'Manifatturiero pesante' },
  { value: 'industry_light', label: 'Manifatturiero leggero' },
  { value: 'food_production', label: 'Produzione alimentare' },
  { value: 'logistics', label: 'Logistica' },
  { value: 'retail_gdo', label: 'Grande distribuzione' },
  { value: 'hospitality_large', label: 'Ricettivo grande' },
  { value: 'hospitality_food_service', label: 'Ristorazione collettiva' },
  { value: 'healthcare', label: 'Sanitario' },
  { value: 'agricultural_intensive', label: 'Agricolo intensivo' },
  { value: 'automotive', label: 'Automotive' },
  { value: 'education', label: 'Istruzione' },
  { value: 'personal_services', label: 'Servizi alla persona' },
  { value: 'professional_offices', label: 'Studi professionali' },
  { value: 'horeca', label: 'HoReCa' },
];

// All Italian province codes for the autocomplete suggestions
const ALL_PROVINCES = [
  'AG','AL','AN','AO','AP','AQ','AR','AT','AV',
  'BA','BG','BI','BL','BN','BO','BR','BS','BT','BZ',
  'CA','CB','CE','CH','CL','CN','CO','CR','CS','CT','CZ',
  'EN',
  'FC','FE','FG','FI','FM','FR',
  'GE','GO','GR',
  'IM','IS',
  'KR',
  'LC','LE','LI','LO','LT','LU',
  'MB','MC','ME','MI','MN','MO','MS','MT',
  'NA','NO','NU',
  'OR',
  'PA','PC','PD','PE','PG','PI','PN','PO','PR','PT','PU','PV','PZ',
  'RA','RC','RE','RG','RI','RM','RN','RO',
  'SA','SI','SO','SP','SR','SS','SU','SV',
  'TA','TE','TN','TO','TP','TR','TS','TV',
  'UD',
  'VA','VB','VC','VE','VI','VR','VT','VV',
];

export function TerritorioConfig() {
  const [selectedGroups, setSelectedGroups] = useState<string[]>([]);
  const [provinces, setProvinces] = useState<string[]>([]);
  const [provinceInput, setProvinceInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  // Load current config on mount
  useEffect(() => {
    getModule('sorgente')
      .then((mod) => {
        const cfg = mod.config as { target_wizard_groups?: string[]; province?: string[] };
        setSelectedGroups(cfg.target_wizard_groups ?? []);
        setProvinces(cfg.province ?? []);
      })
      .catch(() => {/* non-fatal — start with empty */})
      .finally(() => setLoading(false));
  }, []);

  function toggleGroup(value: string) {
    setSelectedGroups((prev) =>
      prev.includes(value) ? prev.filter((g) => g !== value) : [...prev, value],
    );
    setSavedMsg(null);
  }

  function addProvince(code: string) {
    const upper = code.trim().toUpperCase();
    if (!upper || !ALL_PROVINCES.includes(upper) || provinces.includes(upper)) return;
    setProvinces((prev) => [...prev, upper].sort());
    setProvinceInput('');
    setSavedMsg(null);
  }

  function removeProvince(code: string) {
    setProvinces((prev) => prev.filter((p) => p !== code));
    setSavedMsg(null);
  }

  async function handleSave() {
    if (selectedGroups.length === 0) {
      setError('Seleziona almeno un settore.');
      return;
    }
    if (provinces.length === 0) {
      setError('Aggiungi almeno una provincia.');
      return;
    }
    setSaving(true);
    setError(null);
    setSavedMsg(null);
    try {
      // Merge into existing config: preserve other fields (ateco_codes, etc.)
      const current = await getModule('sorgente');
      const existing = current.config as typeof DEFAULT_SORGENTE;
      const merged = {
        ...DEFAULT_SORGENTE,
        ...existing,
        target_wizard_groups: selectedGroups,
        province: provinces,
      };
      await upsertModule('sorgente', { config: merged });
      setSavedMsg(
        `Salvato: ${selectedGroups.length} settori · ${provinces.join(', ')}. ` +
          `Ora puoi rimappare il territorio.`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : 'save_failed');
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <p className="text-xs text-on-surface-variant">Caricamento config…</p>
    );
  }

  const isConfigured = selectedGroups.length > 0 && provinces.length > 0;

  return (
    <div className="rounded-md border border-outline-variant bg-surface-container">
      {/* Header / toggle */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-on-surface">
            Configurazione territorio
          </span>
          {isConfigured ? (
            <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">
              {selectedGroups.length} settori · {provinces.join(', ')}
            </span>
          ) : (
            <span className="rounded-full bg-error/10 px-2 py-0.5 text-xs text-error">
              Non configurato — necessario per mappare
            </span>
          )}
        </div>
        <span className="text-xs text-on-surface-variant">
          {expanded ? '▲ chiudi' : '▼ modifica'}
        </span>
      </button>

      {expanded ? (
        <div className="space-y-5 border-t border-outline-variant px-4 pb-4 pt-4">
          {/* Wizard groups */}
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              Settori target (wizard groups)
            </p>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {WIZARD_GROUPS.map((g) => {
                const active = selectedGroups.includes(g.value);
                return (
                  <button
                    key={g.value}
                    type="button"
                    onClick={() => toggleGroup(g.value)}
                    className={`rounded-md border px-3 py-2 text-left text-xs transition-colors ${
                      active
                        ? 'border-primary bg-primary/10 font-semibold text-primary'
                        : 'border-outline-variant bg-surface-container-high text-on-surface-variant hover:border-primary/50'
                    }`}
                  >
                    {g.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Province */}
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              Province (codice ISO 2 lettere)
            </p>
            {/* Chips */}
            {provinces.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {provinces.map((p) => (
                  <span
                    key={p}
                    className="flex items-center gap-1 rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-semibold text-primary"
                  >
                    {p}
                    <button
                      type="button"
                      onClick={() => removeProvince(p)}
                      className="ml-0.5 text-primary/60 hover:text-error"
                      aria-label={`Rimuovi ${p}`}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-xs text-on-surface-variant">
                Nessuna provincia selezionata.
              </p>
            )}
            {/* Input */}
            <div className="flex gap-2">
              <input
                type="text"
                value={provinceInput}
                onChange={(e) => setProvinceInput(e.target.value.toUpperCase())}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ',' || e.key === ' ') {
                    e.preventDefault();
                    addProvince(provinceInput);
                  }
                }}
                placeholder="es. MN, BS, BG…"
                maxLength={2}
                list="province-list"
                className="w-32 rounded-md border border-outline-variant bg-surface-container px-3 py-1.5 text-xs text-on-surface placeholder-on-surface-variant/50 focus:border-primary focus:outline-none"
              />
              <datalist id="province-list">
                {ALL_PROVINCES.filter((p) => !provinces.includes(p)).map((p) => (
                  <option key={p} value={p} />
                ))}
              </datalist>
              <button
                type="button"
                onClick={() => addProvince(provinceInput)}
                className="rounded-md bg-surface-container-high px-3 py-1.5 text-xs font-semibold text-on-surface hover:bg-outline-variant"
              >
                + Aggiungi
              </button>
            </div>
            <p className="text-xs text-on-surface-variant">
              Digita il codice e premi Invio. Consiglio per il primo test:{' '}
              <strong>MN</strong> (Mantova) — piccola, densa di capannoni.
            </p>
          </div>

          {/* Save */}
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="rounded-full bg-primary px-4 py-2 text-sm font-semibold text-on-primary shadow-ambient-sm transition-colors hover:bg-primary/90 disabled:opacity-50"
            >
              {saving ? 'Salvataggio…' : 'Salva configurazione'}
            </button>
            {savedMsg ? (
              <p className="text-xs text-success">{savedMsg}</p>
            ) : null}
            {error ? (
              <p className="text-xs text-error">{error}</p>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
