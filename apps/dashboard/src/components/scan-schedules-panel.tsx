'use client';

/**
 * ScanSchedulesPanel — gestione UI delle scansioni programmate.
 *
 * Sprint client-feedback E avanzato: il cliente vuole poter
 * configurare scansioni ricorrenti per subset di territori + settori
 * con un budget giornaliero, invece di affidarsi al solo cron 04:30
 * globale.
 *
 * Layout (compatto, sotto le BentoCard /territorio):
 *   ┌────────────────────────────────────────────────────────────┐
 *   │ Programmazione scansioni                  [+ Nuova]        │
 *   │ • Milano industriale — daily, 100/gg, prossima domani      │
 *   │ • Bergamo HORECA   — ogni 3gg, 50/gg, prossima 17/05       │
 *   └────────────────────────────────────────────────────────────┘
 *
 * Quando l'operatore clicca [+ Nuova]:
 *   - apre un form inline con: nome, daily_cap, frequency_days,
 *     sector_filters (chips), start_at (datetime).
 *   - submit → POST /v1/territory/schedules.
 *
 * Per ora il territory_ids è LASCIATO VUOTO (= "tutti i territori
 * mappati"). Verrà esteso in un follow-up con un picker di zone OSM
 * — al momento non c'è una UI per selezionare singole zone.
 */

import { useEffect, useState, type FormEvent } from 'react';

import {
  createScanSchedule,
  deleteScanSchedule,
  listScanSchedules,
  type ScanSchedule,
} from '@/lib/data/territory';
import { SECTOR_LABELS } from '@/lib/sector-labels';
import { relativeTime } from '@/lib/utils';

const SECTORS_FOR_SCHEDULE = Object.keys(SECTOR_LABELS);

const FREQUENCY_OPTIONS: { value: number; label: string }[] = [
  { value: 0, label: 'Una sola volta' },
  { value: 1, label: 'Giornaliera' },
  { value: 3, label: 'Ogni 3 giorni' },
  { value: 7, label: 'Settimanale' },
  { value: 14, label: 'Ogni 2 settimane' },
  { value: 30, label: 'Mensile' },
];

function describe(s: ScanSchedule): string {
  const freq = FREQUENCY_OPTIONS.find((f) => f.value === s.frequency_days);
  return `${freq?.label ?? `Ogni ${s.frequency_days}gg`} · ${s.daily_cap} candidati/giorno`;
}

export function ScanSchedulesPanel() {
  const [items, setItems] = useState<ScanSchedule[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const rows = await listScanSchedules();
      setItems(rows);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'load_failed');
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = event.currentTarget;
    const data = new FormData(form);
    const sectors = data.getAll('sectors').map(String);

    const name = String(data.get('name') ?? '').trim();
    if (!name) {
      setError('Nome obbligatorio');
      return;
    }
    try {
      await createScanSchedule({
        name,
        daily_cap: Number(data.get('daily_cap')) || 100,
        frequency_days: Number(data.get('frequency_days') ?? 1),
        sector_filters: sectors,
        start_at: (data.get('start_at') as string) || undefined,
      });
      form.reset();
      setCreating(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'create_failed');
    }
  }

  async function handleDelete(id: string) {
    if (!confirm('Archiviare questa scansione programmata? Nessuna esecuzione futura.')) return;
    try {
      await deleteScanSchedule(id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'delete_failed');
    }
  }

  return (
    <section className="rounded-2xl bg-surface-container-low p-5 ring-1 ring-on-surface/5">
      <header className="flex items-center justify-between gap-3">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Automazione
          </p>
          <h3 className="font-headline text-lg font-bold tracking-tighter">
            Scansioni programmate
          </h3>
          <p className="mt-1 text-xs text-on-surface-variant">
            Configura scansioni ricorrenti con budget giornaliero. Il sistema
            distribuisce {' '}automaticamente grandi territori (es. 500 candidati
            con cap 100 → 5 giorni).
          </p>
        </div>
        {!creating && (
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="rounded-full bg-primary px-3 py-1.5 text-xs font-semibold text-on-primary hover:opacity-90"
          >
            + Nuova
          </button>
        )}
      </header>

      {/* Create form ------------------------------------------------- */}
      {creating && (
        <form
          onSubmit={handleCreate}
          className="mt-4 space-y-3 rounded-xl bg-surface-container-lowest p-4"
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-xs">
              <span className="font-semibold uppercase tracking-widest text-on-surface-variant">
                Nome
              </span>
              <input
                name="name"
                placeholder="Es. Milano industriale"
                maxLength={120}
                required
                className="w-full rounded-md border border-outline-variant bg-surface px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </label>
            <label className="space-y-1 text-xs">
              <span className="font-semibold uppercase tracking-widest text-on-surface-variant">
                Cap giornaliero
              </span>
              <input
                name="daily_cap"
                type="number"
                defaultValue={100}
                min={1}
                max={5000}
                className="w-full rounded-md border border-outline-variant bg-surface px-3 py-1.5 text-sm tabular-nums focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </label>
            <label className="space-y-1 text-xs">
              <span className="font-semibold uppercase tracking-widest text-on-surface-variant">
                Frequenza
              </span>
              <select
                name="frequency_days"
                defaultValue={1}
                className="w-full rounded-md border border-outline-variant bg-surface px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              >
                {FREQUENCY_OPTIONS.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-xs">
              <span className="font-semibold uppercase tracking-widest text-on-surface-variant">
                Prima esecuzione
              </span>
              <input
                name="start_at"
                type="datetime-local"
                className="w-full rounded-md border border-outline-variant bg-surface px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </label>
          </div>
          <div className="space-y-1.5">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Settori (opzionale — vuoto = tutti)
            </span>
            <div className="flex flex-wrap gap-1.5">
              {SECTORS_FOR_SCHEDULE.map((s) => (
                <label
                  key={s}
                  className="cursor-pointer rounded-full bg-surface-container px-2.5 py-1 text-[11px] ring-1 ring-on-surface/5 hover:bg-surface-container-high has-[:checked]:bg-primary-container has-[:checked]:text-on-primary-container has-[:checked]:ring-primary"
                >
                  <input
                    type="checkbox"
                    name="sectors"
                    value={s}
                    className="sr-only"
                  />
                  {SECTOR_LABELS[s]}
                </label>
              ))}
            </div>
          </div>
          {error && <p className="text-xs text-error">{error}</p>}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setCreating(false)}
              className="rounded-full px-3 py-1.5 text-xs hover:bg-surface-container"
            >
              Annulla
            </button>
            <button
              type="submit"
              className="rounded-full bg-primary px-3 py-1.5 text-xs font-semibold text-on-primary hover:opacity-90"
            >
              Crea
            </button>
          </div>
        </form>
      )}

      {/* List -------------------------------------------------------- */}
      <div className="mt-4">
        {items === null && !loadError && (
          <p className="text-xs text-on-surface-variant">Caricamento…</p>
        )}
        {loadError && (
          <p className="text-xs text-error">Impossibile caricare le programmazioni: {loadError}</p>
        )}
        {items !== null && items.length === 0 && !creating && (
          <p className="text-xs text-on-surface-variant">
            Nessuna scansione programmata. Clicca «+ Nuova» per crearne una.
          </p>
        )}
        {items !== null && items.length > 0 && (
          <ul className="divide-y divide-outline-variant">
            {items.map((s) => (
              <li
                key={s.id}
                className="flex flex-wrap items-center justify-between gap-3 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-on-surface">
                    {s.name}{' '}
                    {s.status === 'paused' && (
                      <span className="ml-2 rounded-full bg-surface-container px-2 py-0.5 text-[9px] uppercase tracking-widest text-on-surface-variant">
                        in pausa
                      </span>
                    )}
                  </p>
                  <p className="mt-0.5 text-xs text-on-surface-variant">
                    {describe(s)} ·{' '}
                    {s.sector_filters.length === 0
                      ? 'tutti i settori'
                      : s.sector_filters.map((x) => SECTOR_LABELS[x] ?? x).join(', ')}
                  </p>
                  <p className="mt-0.5 text-[11px] text-on-surface-variant">
                    Prossima esecuzione:{' '}
                    <strong className="text-on-surface">
                      {new Date(s.next_run_at).toLocaleString('it-IT', {
                        day: '2-digit',
                        month: 'short',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </strong>
                    {s.last_run_at && (
                      <>
                        {' · Ultima: '}
                        <span>{relativeTime(s.last_run_at)}</span>
                        {s.last_run_candidates != null && (
                          <span> ({s.last_run_candidates} candidati)</span>
                        )}
                      </>
                    )}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => handleDelete(s.id)}
                  className="rounded-full bg-surface-container px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant hover:bg-error-container hover:text-on-error-container"
                  aria-label={`Archivia ${s.name}`}
                >
                  Archivia
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
