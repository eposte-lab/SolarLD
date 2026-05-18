'use client';

/**
 * ScanJobCreator — form sinistro della pagina /territorio.
 *
 * Imposta: nome, regione, provincia (opz), comune (opz), settori,
 * cap giornaliero validati, flag "sempre attivo". Submit → POST
 * /v1/territory/scan-jobs → worker parte SUBITO → la lista appare
 * a destra come "in corso".
 */

import { useState, type FormEvent } from 'react';

import { createScanJob } from '@/lib/data/scan-jobs';
import { SECTOR_LABELS } from '@/lib/sector-labels';

// Italian regions (20)
const REGIONI = [
  'Abruzzo', 'Basilicata', 'Calabria', 'Campania', 'Emilia-Romagna',
  'Friuli-Venezia Giulia', 'Lazio', 'Liguria', 'Lombardia', 'Marche',
  'Molise', 'Piemonte', 'Puglia', 'Sardegna', 'Sicilia',
  'Toscana', 'Trentino-Alto Adige', 'Umbria', "Valle d'Aosta", 'Veneto',
];

// Province by region (ISO codes). Subset focalizzato — completa nel
// follow-up con tutte le 107.
const PROVINCE_BY_REGION: Record<string, string[]> = {
  'Abruzzo': ['AQ', 'CH', 'PE', 'TE'],
  'Basilicata': ['MT', 'PZ'],
  'Calabria': ['CS', 'CZ', 'KR', 'RC', 'VV'],
  'Campania': ['AV', 'BN', 'CE', 'NA', 'SA'],
  'Emilia-Romagna': ['BO', 'FC', 'FE', 'MO', 'PC', 'PR', 'RA', 'RE', 'RN'],
  'Friuli-Venezia Giulia': ['GO', 'PN', 'TS', 'UD'],
  'Lazio': ['FR', 'LT', 'RI', 'RM', 'VT'],
  'Liguria': ['GE', 'IM', 'SP', 'SV'],
  'Lombardia': ['BG', 'BS', 'CO', 'CR', 'LC', 'LO', 'MB', 'MI', 'MN', 'PV', 'SO', 'VA'],
  'Marche': ['AN', 'AP', 'FM', 'MC', 'PU'],
  'Molise': ['CB', 'IS'],
  'Piemonte': ['AL', 'AT', 'BI', 'CN', 'NO', 'TO', 'VB', 'VC'],
  'Puglia': ['BA', 'BR', 'BT', 'FG', 'LE', 'TA'],
  'Sardegna': ['CA', 'NU', 'OR', 'SS', 'SU'],
  'Sicilia': ['AG', 'CL', 'CT', 'EN', 'ME', 'PA', 'RG', 'SR', 'TP'],
  'Toscana': ['AR', 'FI', 'GR', 'LI', 'LU', 'MS', 'PI', 'PO', 'PT', 'SI'],
  'Trentino-Alto Adige': ['BZ', 'TN'],
  'Umbria': ['PG', 'TR'],
  "Valle d'Aosta": ['AO'],
  'Veneto': ['BL', 'PD', 'RO', 'TV', 'VE', 'VI', 'VR'],
};

// Settori esclusi dalla scansione Territorio: gli amministratori di
// condominio non sono reperibili su Google Maps — vanno cercati per
// codice ATECO via OpenAPI.it (sezione Scoperta). Restano disponibili
// lì, non qui.
const TERRITORY_EXCLUDED_SECTORS = new Set(['amministratori_condominio']);

const SECTORS = Object.keys(SECTOR_LABELS).filter(
  (s) => !TERRITORY_EXCLUDED_SECTORS.has(s),
);

type Props = {
  onCreated: () => void;
  /** Tetto di "lead validati / giorno" del piano del tenant. */
  maxDailyCap: number;
};

export function ScanJobCreator({ onCreated, maxDailyCap }: Props) {
  const [name, setName] = useState('');
  const [region, setRegion] = useState('');
  const [province, setProvince] = useState('');
  const [comune, setComune] = useState('');
  const [selectedSectors, setSelectedSectors] = useState<Set<string>>(new Set());
  const [dailyCap, setDailyCap] = useState<number>(Math.min(200, maxDailyCap));
  const [totalCap, setTotalCap] = useState<number>(5000);
  const [alwaysActive, setAlwaysActive] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const provinceOptions = region ? PROVINCE_BY_REGION[region] ?? [] : [];

  function toggleSector(s: string) {
    setSelectedSectors((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (!name.trim()) {
      setError('Nome lista obbligatorio');
      return;
    }
    if (!region && !province && !comune.trim()) {
      setError('Seleziona almeno regione, provincia o comune');
      return;
    }

    setSubmitting(true);
    try {
      await createScanJob({
        name: name.trim(),
        region: region || undefined,
        province: province || undefined,
        comune: comune.trim() || undefined,
        sector_filters: Array.from(selectedSectors),
        daily_validated_cap: dailyCap,
        total_validated_cap: totalCap,
        always_active: alwaysActive,
      });
      // Reset
      setName('');
      setRegion('');
      setProvince('');
      setComune('');
      setSelectedSectors(new Set());
      setDailyCap(200);
      setTotalCap(5000);
      setAlwaysActive(false);
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'create_failed');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4 rounded-2xl bg-surface-container-low p-5 ring-1 ring-on-surface/5">
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Nuova scansione
        </p>
        <h3 className="mt-1 font-headline text-lg font-bold tracking-tighter">
          Trova lead per territorio
        </h3>
      </div>

      <label className="block space-y-1">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Nome lista
        </span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Es. NA · automotive"
          maxLength={120}
          required
          className="w-full rounded-md border border-outline-variant bg-surface px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
        />
      </label>

      <label className="block space-y-1">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Regione
        </span>
        <select
          value={region}
          onChange={(e) => { setRegion(e.target.value); setProvince(''); }}
          className="w-full rounded-md border border-outline-variant bg-surface px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
        >
          <option value="">— seleziona —</option>
          {REGIONI.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
      </label>

      <label className="block space-y-1">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Provincia (opzionale)
        </span>
        <select
          value={province}
          onChange={(e) => setProvince(e.target.value)}
          disabled={!region}
          className="w-full rounded-md border border-outline-variant bg-surface px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
        >
          <option value="">— tutta la regione —</option>
          {provinceOptions.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      </label>

      <label className="block space-y-1">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Comune (opzionale)
        </span>
        <input
          value={comune}
          onChange={(e) => setComune(e.target.value)}
          placeholder="Es. Casoria"
          maxLength={120}
          className="w-full rounded-md border border-outline-variant bg-surface px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
        />
      </label>

      <div className="space-y-1.5">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Settori (vuoto = tutti)
        </span>
        <div className="flex flex-wrap gap-1.5">
          {SECTORS.map((s) => (
            <label
              key={s}
              className="cursor-pointer rounded-full bg-surface-container px-2.5 py-1 text-[11px] ring-1 ring-on-surface/5 hover:bg-surface-container-high has-[:checked]:bg-primary-container has-[:checked]:text-on-primary-container has-[:checked]:ring-primary"
            >
              <input
                type="checkbox"
                checked={selectedSectors.has(s)}
                onChange={() => toggleSector(s)}
                className="sr-only"
              />
              {SECTOR_LABELS[s]}
            </label>
          ))}
        </div>
      </div>

      <label className="block space-y-1">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Lead validati / giorno
        </span>
        <input
          type="number"
          value={dailyCap}
          onChange={(e) =>
            setDailyCap(Math.max(1, Math.min(maxDailyCap, Number(e.target.value) || 1)))
          }
          min={1}
          max={maxDailyCap}
          className="w-full rounded-md border border-outline-variant bg-surface px-3 py-1.5 text-sm tabular-nums focus:outline-none focus:ring-1 focus:ring-primary"
        />
        <span className="block text-[10px] text-on-surface-variant">
          Si ferma a questo numero ogni giorno e riprende il giorno dopo —
          massimo {maxDailyCap} con il piano attuale
        </span>
      </label>

      <label className="block space-y-1">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Lead totali (cap)
        </span>
        <input
          type="number"
          value={totalCap}
          onChange={(e) =>
            setTotalCap(Math.max(1, Math.min(50000, Number(e.target.value) || 1)))
          }
          min={1}
          max={50000}
          className="w-full rounded-md border border-outline-variant bg-surface px-3 py-1.5 text-sm tabular-nums focus:outline-none focus:ring-1 focus:ring-primary"
        />
        <span className="block text-[10px] text-on-surface-variant">
          Raggiunto questo totale la scansione si chiude e parte la
          successiva in coda.
        </span>
      </label>

      <label className="flex cursor-pointer items-start gap-2 text-xs">
        <input
          type="checkbox"
          checked={alwaysActive}
          onChange={(e) => setAlwaysActive(e.target.checked)}
          className="mt-0.5"
        />
        <span>
          <strong>Sempre attivo</strong>
          <span className="block text-[11px] text-on-surface-variant">
            Quando i contatti sono esauriti, ricomincia cercando aziende nuove.
            Per territori predefiniti come una regione intera.
          </span>
        </span>
      </label>

      {error && <p className="text-xs text-error">⚠ {error}</p>}

      <button
        type="submit"
        disabled={submitting}
        className="w-full rounded-full bg-primary px-4 py-2.5 text-sm font-semibold text-on-primary hover:opacity-90 disabled:opacity-50"
      >
        {submitting ? 'Avvio…' : '▶ Avvia scansione'}
      </button>
    </form>
  );
}
