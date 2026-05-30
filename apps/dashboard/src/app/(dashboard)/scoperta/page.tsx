'use client';

/**
 * /scoperta — Trova aziende v3 (Google Places).
 *
 * Operatore-driven discovery via Google Places: settore + comune/provincia +
 * raggio + keyword opzionale. I risultati possono essere salvati come lista
 * persistente (`prospect_lists`); la lista può poi essere convalidata per il
 * fotovoltaico (esegue L2-L4 funnel inline) e infine lanciato l'outreach
 * on-demand. Tutti gli outreach passano per il daily cap esistente.
 *
 * Differenza vs Atoka legacy: niente filtri ATECO/employees/revenue; il
 * settore (wizard_group) determina i Google `includedPrimaryTypes`.
 */

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { GradientButton } from '@/components/ui/gradient-button';
import { SectionEyebrow } from '@/components/ui/section-eyebrow';
import { ApiError } from '@/lib/api-client';
import {
  createList,
  fetchSectors,
  listProspectLists,
  searchProspector,
  type ProspectList,
  type ProspectorPlace,
} from '@/lib/data/prospector';
import { sectorLabel, SECTOR_LABELS } from '@/lib/sector-labels';
import { formatNumber } from '@/lib/utils';
import { AlertTriangle, Star } from 'lucide-react';

// Italian provinces (ISO 3166-2:IT) — same set as
// `apps/api/src/data/province_centroids.py`.
const PROVINCES: string[] = [
  'AG', 'AL', 'AN', 'AO', 'AP', 'AQ', 'AR', 'AT', 'AV', 'BA',
  'BG', 'BI', 'BL', 'BN', 'BO', 'BR', 'BS', 'BT', 'BZ', 'CA',
  'CB', 'CE', 'CH', 'CL', 'CN', 'CO', 'CR', 'CS', 'CT', 'CZ',
  'EN', 'FC', 'FE', 'FG', 'FI', 'FM', 'FR', 'GE', 'GO', 'GR',
  'IM', 'IS', 'KR', 'LC', 'LE', 'LI', 'LO', 'LT', 'LU', 'MB',
  'MC', 'ME', 'MI', 'MN', 'MO', 'MS', 'MT', 'NA', 'NO', 'NU',
  'OR', 'PA', 'PC', 'PD', 'PE', 'PG', 'PI', 'PN', 'PO', 'PR',
  'PT', 'PU', 'PV', 'PZ', 'RA', 'RC', 'RE', 'RG', 'RI', 'RM',
  'RN', 'RO', 'SA', 'SI', 'SO', 'SP', 'SR', 'SS', 'SU', 'SV',
  'TA', 'TE', 'TN', 'TO', 'TP', 'TR', 'TS', 'TV', 'UD', 'VA',
  'VB', 'VC', 'VE', 'VI', 'VR', 'VT', 'VV',
];

interface FormState {
  sector: string;
  province_code: string;
  comune: string;
  radius_km: number;
  keyword: string;
  limit: number;
}

const INITIAL: FormState = {
  sector: '',
  province_code: '',
  comune: '',
  radius_km: 30,
  keyword: '',
  limit: 60,
};

export default function ScopertaPage() {
  const [availableSectors, setAvailableSectors] = useState<string[]>([]);
  const [form, setForm] = useState<FormState>(INITIAL);
  const [items, setItems] = useState<ProspectorPlace[]>([]);
  const [searchMeta, setSearchMeta] = useState<{
    count: number;
    isDemo: boolean;
  } | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [saving, setSaving] = useState(false);
  const [savedListId, setSavedListId] = useState<string | null>(null);
  const [recentLists, setRecentLists] = useState<ProspectList[]>([]);
  // Campaign type drives whether the saved list goes through the
  // L4 Solar gate ('solar_rooftop' = standard rooftop validation) or
  // bypasses it ('generic_outreach' = non-rooftop B2B campaign with
  // a custom HTML email template, e.g. amministratori condominio).
  const [campaignType, setCampaignType] = useState<
    'solar_rooftop' | 'generic_outreach'
  >('solar_rooftop');

  useEffect(() => {
    fetchSectors()
      .then(setAvailableSectors)
      .catch(() => {
        // Fallback: hard-coded sector slugs from SECTOR_LABELS keys.
        setAvailableSectors(Object.keys(SECTOR_LABELS));
      });
    listProspectLists({ page: 1, page_size: 5 })
      .then((res) => setRecentLists(res.rows))
      .catch(() => setRecentLists([]));
  }, []);

  const sectorOptions = useMemo(
    () =>
      availableSectors
        .map((s) => ({ value: s, label: sectorLabel(s) }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [availableSectors],
  );

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
    setSavedListId(null);
  }

  async function runSearch() {
    setError(null);
    setSavedListId(null);

    if (!form.sector) {
      setError('Seleziona un settore.');
      return;
    }
    if (!form.province_code && !form.comune.trim()) {
      setError('Seleziona una provincia o specifica un comune.');
      return;
    }

    setSearching(true);
    try {
      const res = await searchProspector({
        sector: form.sector,
        province_code: form.province_code || undefined,
        comune: form.comune.trim() || undefined,
        radius_km: form.radius_km,
        keyword: form.keyword.trim() || undefined,
        limit: form.limit,
      });
      setItems(res.items);
      setSearchMeta({ count: res.count, isDemo: res.is_demo_data ?? false });
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
    if (items.length === 0) return;
    setSaving(true);
    try {
      const typeSuffix = campaignType === 'generic_outreach' ? ' · custom' : '';
      const res = await createList({
        name: `${sectorLabel(form.sector)} · ${form.comune || form.province_code || 'IT'} (${items.length})${typeSuffix}`,
        description: `Ricerca v3: settore=${form.sector}, raggio=${form.radius_km}km${form.keyword ? `, keyword="${form.keyword}"` : ''}`,
        search_filter: { ...form, campaign_type: campaignType },
        items,
        campaign_type: campaignType,
      });
      setSavedListId(res.id);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status} — ${err.message}`
          : err instanceof Error
            ? err.message
            : 'Errore inatteso';
      setError(msg);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <SectionEyebrow>Trova aziende</SectionEyebrow>
        <h1 className="font-headline text-2xl font-bold tracking-tighter md:text-4xl">
          Trova aziende
        </h1>
        <p className="mt-2 max-w-3xl text-sm text-on-surface-variant">
          Cerca aziende reali per settore e area. Salva una lista, poi
          convalida per il fotovoltaico e lancia l&apos;outreach. Gli invii
          rispettano il cap giornaliero.
        </p>
      </header>

      <BentoGrid cols={3}>
        {/* Filters */}
        <BentoCard span="full" className="md:col-span-1 md:row-span-2">
          <SectionEyebrow tone="dim">Filtri</SectionEyebrow>
          <div className="mt-3 space-y-4">
            <div>
              <label className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Settore
              </label>
              <select
                value={form.sector}
                onChange={(e) => update('sector', e.target.value)}
                className="mt-1 w-full rounded-md bg-surface-container-low px-3 py-2 text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary"
              >
                <option value="">— scegli —</option>
                {sectorOptions.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Provincia
              </label>
              <select
                value={form.province_code}
                onChange={(e) => update('province_code', e.target.value)}
                className="mt-1 w-full rounded-md bg-surface-container-low px-3 py-2 text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary"
              >
                <option value="">— scegli —</option>
                {PROVINCES.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Comune (opzionale)
              </label>
              <input
                type="text"
                placeholder="Es. Brescia"
                value={form.comune}
                onChange={(e) => update('comune', e.target.value)}
                className="mt-1 w-full rounded-md bg-surface-container-low px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>

            <div>
              <label className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Raggio · {form.radius_km} km
              </label>
              <input
                type="range"
                min={5}
                max={50}
                step={5}
                value={form.radius_km}
                onChange={(e) => update('radius_km', Number(e.target.value))}
                className="mt-2 w-full"
              />
            </div>

            <div>
              <label className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Keyword (opzionale)
              </label>
              <input
                type="text"
                placeholder="Es. carpenteria, capannone…"
                value={form.keyword}
                onChange={(e) => update('keyword', e.target.value)}
                className="mt-1 w-full rounded-md bg-surface-container-low px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>

            <div>
              <label className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Limite risultati
              </label>
              <select
                value={form.limit}
                onChange={(e) => update('limit', Number(e.target.value))}
                className="mt-1 w-full rounded-md bg-surface-container-low px-3 py-2 text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary"
              >
                <option value={20}>20</option>
                <option value={60}>60 (default)</option>
                <option value={100}>100</option>
                <option value={200}>200</option>
              </select>
            </div>

            <div className="space-y-2 pt-2">
              <button
                type="button"
                onClick={runSearch}
                disabled={searching}
                className="w-full rounded-full bg-primary px-4 py-2 text-sm font-semibold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-95 disabled:opacity-50"
              >
                {searching ? 'Cerco…' : 'Cerca'}
              </button>
              {error && (
                <div className="flex items-start gap-2 rounded-lg bg-error-container/50 px-3 py-2 text-xs text-on-error-container">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  <span>{error}</span>
                </div>
              )}
            </div>
          </div>
        </BentoCard>

        {/* Results */}
        <BentoCard span="full" className="md:col-span-2 md:row-span-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <SectionEyebrow tone="dim">Risultati</SectionEyebrow>
              <p className="mt-1 text-sm text-on-surface-variant">
                {searchMeta
                  ? `${formatNumber(searchMeta.count)} aziende trovate`
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
                    {saving ? 'Salvo…' : 'Salva lista'}
                  </button>
                )}
              </div>
            )}
          </div>

          {/* Campaign-type selector — visible only when there are items
              ready to save. The choice changes the validation flow:
              'solar_rooftop' goes through L4 Solar gate (default),
              'generic_outreach' bypasses Solar for non-rooftop campaigns. */}
          {items.length > 0 && !savedListId && (
            <fieldset className="mt-4 rounded-lg bg-surface-container-low p-3">
              <legend className="px-2 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Tipo campagna
              </legend>
              <div className="grid gap-2 sm:grid-cols-2">
                <label
                  className={`flex cursor-pointer items-start gap-2 rounded-md border p-2 text-xs transition-colors ${
                    campaignType === 'solar_rooftop'
                      ? 'border-primary bg-primary/10 text-on-surface'
                      : 'border-outline-variant/30 text-on-surface-variant hover:border-primary/40'
                  }`}
                >
                  <input
                    type="radio"
                    name="campaign-type"
                    value="solar_rooftop"
                    checked={campaignType === 'solar_rooftop'}
                    onChange={() => setCampaignType('solar_rooftop')}
                    className="mt-0.5 accent-primary"
                  />
                  <div>
                    <div className="text-sm font-semibold">Tetto fotovoltaico</div>
                    <div className="opacity-80">
                      Convalida con Google Solar (area ≥ 200 m², kW ≥ 60).
                      Email + render impianto auto-generati.
                    </div>
                  </div>
                </label>
                <label
                  className={`flex cursor-pointer items-start gap-2 rounded-md border p-2 text-xs transition-colors ${
                    campaignType === 'generic_outreach'
                      ? 'border-primary bg-primary/10 text-on-surface'
                      : 'border-outline-variant/30 text-on-surface-variant hover:border-primary/40'
                  }`}
                >
                  <input
                    type="radio"
                    name="campaign-type"
                    value="generic_outreach"
                    checked={campaignType === 'generic_outreach'}
                    onChange={() => setCampaignType('generic_outreach')}
                    className="mt-0.5 accent-primary"
                  />
                  <div>
                    <div className="text-sm font-semibold">
                      Servizio personalizzato
                    </div>
                    <div className="opacity-80">
                      Niente convalida Solar. Tutte le aziende trovate diventano
                      lead con email custom HTML (amministratori condominio,
                      cliniche, ecc.).
                    </div>
                  </div>
                </label>
              </div>
            </fieldset>
          )}

          {/* Demo placeholder banner — visible only when the API tagged
              the response with `is_demo_data: true`. It means the
              tenant is `is_demo` and the OpenAPI.it token is empty,
              so we returned a hand-curated sample for the walkthrough.
              Production tenants never see this. */}
          {searchMeta?.isDemo && items.length > 0 && (
            <div className="mt-4 rounded-lg bg-tertiary-container px-4 py-3 text-sm text-on-tertiary-container">
              <p className="font-semibold">Dati di esempio</p>
              <p className="mt-0.5 text-xs opacity-90">
                Questi sono amministratori di condominio campione mostrati
                per la demo. Per ottenere dati reali dal Registro Imprese
                italiano, configura il token API (OpenAPI.it) nelle
                impostazioni.
              </p>
            </div>
          )}

          {items.length === 0 ? (
            <div className="mt-4 space-y-4">
              <div className="rounded-lg bg-surface-container-low p-8 text-center text-sm text-on-surface-variant">
                Configura i filtri a sinistra e premi <strong>Cerca</strong>.
              </div>
              {recentLists.length > 0 && (
                <div className="rounded-lg bg-surface-container-low p-4">
                  <div className="mb-2 flex items-center justify-between">
                    <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                      Le tue liste recenti
                    </p>
                    <Link
                      href="/scoperta/liste"
                      className="text-xs font-semibold text-primary hover:underline"
                    >
                      Vedi tutte →
                    </Link>
                  </div>
                  <ul className="divide-y divide-on-surface/5">
                    {recentLists.map((l) => (
                      <li key={l.id}>
                        <Link
                          href={`/scoperta/liste/${l.id}`}
                          className="flex items-center justify-between gap-3 py-2.5 transition-colors hover:bg-surface-container-high/40"
                        >
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-semibold text-on-surface">
                              {l.name}
                            </p>
                            <p className="text-[11px] text-on-surface-variant">
                              {formatNumber(l.item_count)} aziende
                              {l.outreach_started_at
                                ? ' · outreach lanciato'
                                : l.validation_completed_at
                                  ? ' · pronta per outreach'
                                  : l.validation_started_at
                                    ? ' · convalida in corso'
                                    : ' · da convalidare'}
                            </p>
                          </div>
                          <span className="shrink-0 text-xs text-primary">
                            apri →
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <div className="mt-4 overflow-hidden rounded-lg bg-surface-container-low">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                    <th className="px-4 py-3">Azienda</th>
                    <th className="px-4 py-3">Indirizzo</th>
                    <th className="px-4 py-3 text-center">Rating</th>
                    <th className="px-4 py-3 text-right">Recensioni</th>
                    <th className="px-4 py-3">Sito</th>
                    <th className="px-4 py-3">Tel</th>
                  </tr>
                </thead>
                <tbody className="bg-surface-container-lowest">
                  {items.map((p, idx) => (
                    <tr
                      key={p.google_place_id}
                      className="transition-colors hover:bg-surface-container-low"
                      style={
                        idx !== 0
                          ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }
                          : undefined
                      }
                    >
                      <td className="px-4 py-3">
                        <div className="font-semibold text-on-surface">
                          {p.display_name ?? '—'}
                        </div>
                        {p.google_maps_uri && (
                          <a
                            href={p.google_maps_uri}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-[10px] text-primary hover:underline"
                          >
                            apri su Google Maps ↗
                          </a>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-on-surface-variant">
                        {p.formatted_address ?? '—'}
                      </td>
                      <td className="px-4 py-3 text-center text-xs">
                        {p.rating != null ? (
                          <span className="inline-flex items-center gap-1 text-on-surface">
                            <Star size={10} className="fill-warning text-warning" />
                            {p.rating.toFixed(1)}
                          </span>
                        ) : (
                          <span className="text-on-surface-variant">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-xs text-on-surface-variant">
                        {p.user_ratings_total ?? '—'}
                      </td>
                      <td className="px-4 py-3 text-xs">
                        {p.website ? (
                          <a
                            href={p.website}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-primary hover:underline"
                          >
                            sito ↗
                          </a>
                        ) : (
                          <span className="text-on-surface-variant">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-on-surface-variant">
                        {p.phone ? (
                          <a href={`tel:${p.phone}`} className="hover:text-on-surface">
                            {p.phone}
                          </a>
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </BentoCard>
      </BentoGrid>

      <BentoCard span="full">
        <SectionEyebrow tone="dim">Le tue liste</SectionEyebrow>
        <p className="mt-2 text-sm text-on-surface-variant">
          <Link href="/scoperta/liste" className="text-primary hover:underline">
            Vedi tutte le liste salvate →
          </Link>{' '}
          Da lì puoi convalidare per il fotovoltaico (esegue scraping +
          Solar API) e lanciare l&apos;outreach.
        </p>
      </BentoCard>
    </div>
  );
}
