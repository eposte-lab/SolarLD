'use client';

/**
 * /scoperta — "Trova aziende" prospector page (Editorial Glass).
 *
 * Standalone discovery surface. Lets the operator search Atoka by
 * preset chip (Amministratori condominio, Capannoni industriali, …)
 * or custom ATECO codes + filters (provincia, dipendenti, fatturato,
 * keyword), preview live results, and persist them as a saved list
 * for later import / campaign launch.
 *
 * Distinct from /contatti: contatti = automated funnel output.
 * /scoperta = manual one-shot prospecting, no scoring, no funnel.
 *
 * Layout:
 *   - Hero header con eyebrow + headline
 *   - Preset chip strip (BentoCard glass)
 *   - 2-col bento: filtri (sx, span 1) + risultati (dx, span 3)
 *   - Cost preview pill + "Salva lista" CTA
 */

import {
  AlertTriangle,
  Building2,
  Euro,
  Loader2,
  MapPin,
  Save,
  Search,
  Users,
} from 'lucide-react';
import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { GradientButton } from '@/components/ui/gradient-button';
import { SectionEyebrow } from '@/components/ui/section-eyebrow';
import { SortableTh } from '@/components/ui/sortable-th';
import { useSortableData } from '@/hooks/use-sortable-data';
import { ApiError } from '@/lib/api-client';
import {
  type AtecoPreset,
  type ProspectorItem,
  createList,
  fetchPresets,
  searchProspector,
} from '@/lib/data/prospector';
import { cn, formatNumber } from '@/lib/utils';

const PAGE_SIZE = 50;

interface FormState {
  preset_code: string;
  ateco_codes_text: string;
  province_code: string;
  region_code: string;
  employees_min: string;
  employees_max: string;
  revenue_min_eur: string;
  revenue_max_eur: string;
  keyword: string;
}

const INITIAL: FormState = {
  preset_code: '',
  ateco_codes_text: '',
  province_code: '',
  region_code: '',
  employees_min: '',
  employees_max: '',
  revenue_min_eur: '',
  revenue_max_eur: '',
  keyword: '',
};

function parseAtecoCodes(text: string): string[] {
  return text
    .split(/[\s,;]+/)
    .map((c) => c.trim())
    .filter(Boolean);
}

function toIntOrUndefined(v: string): number | undefined {
  if (!v.trim()) return undefined;
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 ? n : undefined;
}

export default function ScopertaPage() {
  const [presets, setPresets] = useState<Record<string, AtecoPreset>>({});
  const [loadingPresets, setLoadingPresets] = useState(true);
  const [form, setForm] = useState<FormState>(INITIAL);
  const [items, setItems] = useState<ProspectorItem[]>([]);
  const [searchMeta, setSearchMeta] = useState<{
    count: number;
    estimated_cost_eur: number;
  } | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [saving, setSaving] = useState(false);
  const [savedListId, setSavedListId] = useState<string | null>(null);

  const { sorted: sortedItems, sortKey, sortDir, requestSort } = useSortableData<
    ProspectorItem,
    'name' | 'ateco' | 'sede' | 'employees' | 'revenue'
  >(items, (it, key) => {
    switch (key) {
      case 'name':
        return it.legal_name ?? '';
      case 'ateco':
        return it.ateco_code ?? '';
      case 'sede':
        return it.hq_city ?? '';
      case 'employees':
        return it.employees ?? null;
      case 'revenue':
        return it.revenue_eur ?? null;
    }
  });

  useEffect(() => {
    fetchPresets()
      .then(setPresets)
      .catch(() => {
        // Non-fatal — custom ATECO entry still works.
      })
      .finally(() => setLoadingPresets(false));
  }, []);

  const activePreset: AtecoPreset | null = form.preset_code
    ? presets[form.preset_code] ?? null
    : null;

  const effectiveAtecoCodes = useMemo(() => {
    if (activePreset) return activePreset.ateco_codes;
    return parseAtecoCodes(form.ateco_codes_text);
  }, [activePreset, form.ateco_codes_text]);

  function selectPreset(code: string) {
    setForm((prev) => ({
      ...prev,
      preset_code: prev.preset_code === code ? '' : code,
      ateco_codes_text: '',
    }));
    setSavedListId(null);
  }

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
    if (key !== 'keyword') setSavedListId(null);
  }

  async function runSearch() {
    setError(null);
    setSavedListId(null);

    if (effectiveAtecoCodes.length === 0) {
      setError('Seleziona un preset o inserisci almeno un codice ATECO.');
      return;
    }

    setSearching(true);
    try {
      const res = await searchProspector({
        ateco_codes: effectiveAtecoCodes,
        province_code: form.province_code.trim() || undefined,
        region_code: form.region_code.trim() || undefined,
        employees_min: toIntOrUndefined(form.employees_min),
        employees_max: toIntOrUndefined(form.employees_max),
        revenue_min_eur: toIntOrUndefined(form.revenue_min_eur),
        revenue_max_eur: toIntOrUndefined(form.revenue_max_eur),
        keyword: form.keyword.trim() || undefined,
        limit: PAGE_SIZE,
        offset: 0,
        preset_code: form.preset_code || undefined,
      });
      setItems(res.items);
      setSearchMeta({
        count: res.count,
        estimated_cost_eur: res.estimated_cost_eur,
      });
      if (res.error) setError(`Errore ricerca: ${res.error}`);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status} — ${err.message}`
          : err instanceof Error
            ? err.message
            : 'Errore inatteso';
      setError(msg);
      setItems([]);
      setSearchMeta(null);
    } finally {
      setSearching(false);
    }
  }

  async function saveList() {
    if (!items.length) return;
    const defaultName = activePreset
      ? `${activePreset.label}${form.province_code ? ` — ${form.province_code.toUpperCase()}` : ''}`
      : `Ricerca ATECO ${effectiveAtecoCodes.slice(0, 2).join(', ')}`;
    const name = window.prompt('Nome della lista', defaultName);
    if (!name) return;

    setSaving(true);
    setError(null);
    try {
      const list = await createList({
        name,
        description: form.keyword
          ? `Keyword: "${form.keyword}"`
          : undefined,
        search_filter: {
          preset_code: form.preset_code || null,
          ateco_codes: effectiveAtecoCodes,
          province_code: form.province_code || null,
          region_code: form.region_code || null,
          employees_min: toIntOrUndefined(form.employees_min) ?? null,
          employees_max: toIntOrUndefined(form.employees_max) ?? null,
          revenue_min_eur: toIntOrUndefined(form.revenue_min_eur) ?? null,
          revenue_max_eur: toIntOrUndefined(form.revenue_max_eur) ?? null,
          keyword: form.keyword || null,
        },
        preset_code: form.preset_code || undefined,
        items,
      });
      setSavedListId(list.id);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status} — ${err.message}`
          : err instanceof Error
            ? err.message
            : 'Errore salvataggio';
      setError(msg);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <SectionEyebrow tone="mint" icon={<Search size={11} strokeWidth={2.25} />}>
            Discovery · Ricerca aziende
          </SectionEyebrow>
          <h1 className="mt-1.5 font-headline text-4xl font-bold tracking-tighter text-on-surface">
            Trova aziende
          </h1>
          <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
            Ricerca per codice ATECO, geografia, dipendenti e fatturato.
            I risultati possono essere salvati come lista e successivamente
            importati nel funnel o lanciati come campagna.
          </p>
        </div>
        <Link
          href="/scoperta/liste"
          className="rounded-full bg-surface-container-high px-4 py-2 text-sm font-semibold text-on-surface transition-opacity hover:opacity-80"
        >
          Liste salvate →
        </Link>
      </header>

      {/* Preset strip */}
      <BentoCard variant="glass" padding="default" span="full">
        <SectionEyebrow tone="dim">Preset rapidi</SectionEyebrow>
        <div className="mt-3 flex flex-wrap gap-2">
          {loadingPresets && (
            <span className="inline-flex items-center gap-2 text-sm text-on-surface-variant">
              <Loader2 size={14} className="animate-spin" /> Carico preset…
            </span>
          )}
          {!loadingPresets && Object.keys(presets).length === 0 && (
            <span className="text-sm text-on-surface-variant">
              Nessun preset disponibile.
            </span>
          )}
          {Object.entries(presets).map(([code, preset]) => {
            const active = form.preset_code === code;
            return (
              <button
                key={code}
                type="button"
                onClick={() => selectPreset(code)}
                className={cn(
                  'rounded-full px-4 py-2 text-sm font-medium transition-all duration-150',
                  active
                    ? 'bg-primary text-on-primary shadow-ambient-sm ring-1 ring-primary/40'
                    : 'bg-surface-container-low text-on-surface-variant hover:bg-surface-container hover:text-on-surface ghost-border',
                )}
                title={preset.description}
              >
                {preset.label}
              </button>
            );
          })}
        </div>
        {activePreset && (
          <p className="mt-3 text-xs leading-relaxed text-on-surface-variant">
            <span className="font-medium text-on-surface">
              ATECO inclusi:
            </span>{' '}
            {activePreset.ateco_codes.join(', ')} · {activePreset.description}
          </p>
        )}
      </BentoCard>

      {/* Filters + results grid */}
      <BentoGrid cols={4}>
        {/* Filters */}
        <BentoCard variant="default" padding="default" span="1x1" className="md:col-span-1 md:row-span-2">
          <SectionEyebrow tone="mint" icon={<Search size={11} />}>
            Filtri
          </SectionEyebrow>

          <div className="mt-5 space-y-5">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                Codici ATECO {!activePreset && '(uno per riga, virgola o spazio)'}
              </label>
              <textarea
                rows={2}
                disabled={!!activePreset}
                value={
                  activePreset
                    ? activePreset.ateco_codes.join(', ')
                    : form.ateco_codes_text
                }
                onChange={(e) => update('ateco_codes_text', e.target.value)}
                placeholder="68.32.00, 81.10.00"
                className="w-full rounded-lg bg-surface-container-low px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-muted outline-none focus:ring-2 focus:ring-primary/40 disabled:opacity-50"
              />
              {activePreset && (
                <p className="text-[11px] text-on-surface-muted">
                  Bloccato dal preset. Deseleziona il preset per editare manualmente.
                </p>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <label className="text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                  Provincia (sigla)
                </label>
                <input
                  maxLength={2}
                  value={form.province_code}
                  onChange={(e) => update('province_code', e.target.value.toUpperCase())}
                  placeholder="NA"
                  className="w-full rounded-lg bg-surface-container-low px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-muted outline-none focus:ring-2 focus:ring-primary/40 uppercase"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                  Regione (codice)
                </label>
                <input
                  value={form.region_code}
                  onChange={(e) => update('region_code', e.target.value)}
                  placeholder="15"
                  className="w-full rounded-lg bg-surface-container-low px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-muted outline-none focus:ring-2 focus:ring-primary/40"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                Dipendenti
              </label>
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  min={0}
                  value={form.employees_min}
                  onChange={(e) => update('employees_min', e.target.value)}
                  placeholder="min"
                  className="w-full rounded-lg bg-surface-container-low px-3 py-2 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
                />
                <span className="text-on-surface-muted">·</span>
                <input
                  type="number"
                  min={0}
                  value={form.employees_max}
                  onChange={(e) => update('employees_max', e.target.value)}
                  placeholder="max"
                  className="w-full rounded-lg bg-surface-container-low px-3 py-2 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                Fatturato (€)
              </label>
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  min={0}
                  value={form.revenue_min_eur}
                  onChange={(e) => update('revenue_min_eur', e.target.value)}
                  placeholder="min"
                  className="w-full rounded-lg bg-surface-container-low px-3 py-2 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
                />
                <span className="text-on-surface-muted">·</span>
                <input
                  type="number"
                  min={0}
                  value={form.revenue_max_eur}
                  onChange={(e) => update('revenue_max_eur', e.target.value)}
                  placeholder="max"
                  className="w-full rounded-lg bg-surface-container-low px-3 py-2 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                Keyword (post-filtro)
              </label>
              <input
                value={form.keyword}
                onChange={(e) => update('keyword', e.target.value)}
                placeholder="es. condominio"
                className="w-full rounded-lg bg-surface-container-low px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-muted outline-none focus:ring-2 focus:ring-primary/40"
              />
            </div>

            <GradientButton
              onClick={runSearch}
              size="md"
              className="w-full justify-center"
              disabled={searching || effectiveAtecoCodes.length === 0}
            >
              {searching ? (
                <>
                  <Loader2 size={16} className="animate-spin" /> Cerco…
                </>
              ) : (
                <>
                  <Search size={16} strokeWidth={2.25} /> Avvia ricerca
                </>
              )}
            </GradientButton>

            {error && (
              <div className="flex items-start gap-2 rounded-lg bg-error-container/50 px-3 py-2 text-xs text-on-error-container">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                <span>{error}</span>
              </div>
            )}
          </div>
        </BentoCard>

        {/* Results */}
        <BentoCard
          variant="default"
          padding="default"
          span="full"
          className="md:col-span-3 md:row-span-2"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <SectionEyebrow tone="dim">Risultati</SectionEyebrow>
              <p className="mt-1 text-sm text-on-surface-variant">
                {searchMeta
                  ? `${formatNumber(searchMeta.count)} aziende · costo stimato €${searchMeta.estimated_cost_eur.toFixed(2)}`
                  : 'Avvia una ricerca per popolare la tabella.'}
              </p>
            </div>
            {items.length > 0 && (
              <div className="flex items-center gap-2">
                {savedListId ? (
                  <Link
                    href={`/scoperta/liste/${savedListId}`}
                    className="rounded-full bg-primary/15 px-4 py-2 text-sm font-semibold text-primary transition-opacity hover:opacity-80"
                  >
                    Lista salvata →
                  </Link>
                ) : (
                  <button
                    type="button"
                    onClick={saveList}
                    disabled={saving}
                    className="inline-flex items-center gap-2 rounded-full bg-primary px-4 py-2 text-sm font-semibold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-95 disabled:opacity-50"
                  >
                    {saving ? (
                      <>
                        <Loader2 size={14} className="animate-spin" /> Salvo…
                      </>
                    ) : (
                      <>
                        <Save size={14} strokeWidth={2.25} /> Salva lista
                      </>
                    )}
                  </button>
                )}
              </div>
            )}
          </div>

          <div className="mt-5 -mx-2 overflow-x-auto">
            <table className="w-full min-w-[820px] border-separate border-spacing-y-1 text-sm">
              <thead>
                <tr>
                  <SortableTh sortKey="name" active={sortKey} dir={sortDir} onSort={requestSort} className="px-2 py-2">Azienda</SortableTh>
                  <SortableTh sortKey="ateco" active={sortKey} dir={sortDir} onSort={requestSort} className="px-2 py-2">ATECO</SortableTh>
                  <SortableTh sortKey="sede" active={sortKey} dir={sortDir} onSort={requestSort} className="px-2 py-2">Sede</SortableTh>
                  <SortableTh sortKey="employees" active={sortKey} dir={sortDir} onSort={requestSort} className="px-2 py-2" align="right">Dipendenti</SortableTh>
                  <SortableTh sortKey="revenue" active={sortKey} dir={sortDir} onSort={requestSort} className="px-2 py-2" align="right">Fatturato (€)</SortableTh>
                </tr>
              </thead>
              <tbody>
                {!searching && items.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-2 py-10 text-center text-sm text-on-surface-variant">
                      <Building2 size={28} className="mx-auto mb-2 opacity-40" strokeWidth={1.5} />
                      Nessun risultato. Imposta filtri e premi “Avvia ricerca”.
                    </td>
                  </tr>
                )}
                {searching && (
                  <tr>
                    <td colSpan={5} className="px-2 py-10 text-center text-sm text-on-surface-variant">
                      <Loader2 size={20} className="mx-auto mb-2 animate-spin" />
                      Ricerca aziende in corso…
                    </td>
                  </tr>
                )}
                {sortedItems.map((it) => (
                  <tr
                    key={it.vat_number ?? it.legal_name ?? Math.random()}
                    className="bg-surface-container-low/60 transition-colors hover:bg-surface-container"
                  >
                    <td className="px-2 py-3 align-top">
                      <div className="font-semibold text-on-surface">
                        {it.legal_name || '—'}
                      </div>
                      {it.vat_number && (
                        <div className="text-[11px] uppercase tracking-wide text-on-surface-muted">
                          P.IVA {it.vat_number}
                        </div>
                      )}
                      {it.website_domain && (
                        <a
                          href={`https://${it.website_domain}`}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[11px] text-primary hover:underline"
                        >
                          {it.website_domain}
                        </a>
                      )}
                    </td>
                    <td className="px-2 py-3 align-top">
                      <div className="font-mono text-xs text-on-surface">
                        {it.ateco_code || '—'}
                      </div>
                      {it.ateco_description && (
                        <div className="text-[11px] text-on-surface-variant line-clamp-2">
                          {it.ateco_description}
                        </div>
                      )}
                    </td>
                    <td className="px-2 py-3 align-top">
                      {it.hq_city ? (
                        <div className="flex items-start gap-1 text-on-surface">
                          <MapPin size={12} strokeWidth={1.75} className="mt-0.5 shrink-0 text-on-surface-muted" />
                          <div>
                            <div>
                              {it.hq_city}
                              {it.hq_province && (
                                <span className="text-on-surface-muted"> · {it.hq_province}</span>
                              )}
                            </div>
                            {it.hq_cap && (
                              <div className="text-[11px] text-on-surface-muted">
                                CAP {it.hq_cap}
                              </div>
                            )}
                          </div>
                        </div>
                      ) : (
                        <span className="text-on-surface-muted">—</span>
                      )}
                    </td>
                    <td className="px-2 py-3 text-right align-top tabular-nums">
                      {it.employees != null ? (
                        <span className="inline-flex items-center gap-1 text-on-surface">
                          <Users size={12} strokeWidth={1.75} className="text-on-surface-muted" />
                          {formatNumber(it.employees)}
                        </span>
                      ) : (
                        <span className="text-on-surface-muted">—</span>
                      )}
                    </td>
                    <td className="px-2 py-3 text-right align-top tabular-nums">
                      {it.revenue_eur != null ? (
                        <span className="inline-flex items-center gap-1 text-on-surface">
                          <Euro size={12} strokeWidth={1.75} className="text-on-surface-muted" />
                          {formatNumber(it.revenue_eur)}
                        </span>
                      ) : (
                        <span className="text-on-surface-muted">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </BentoCard>
      </BentoGrid>
    </div>
  );
}
