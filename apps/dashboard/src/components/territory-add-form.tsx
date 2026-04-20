'use client';

/**
 * TerritoryAddForm — client component for /territories.
 *
 * Replaces the static server-rendered form so we can offer
 * one-click bbox auto-resolution via Nominatim (OpenStreetMap).
 *
 * UX:
 *   1. User picks type and enters a code (CAP / comune / provincia / regione).
 *   2. On blur of the code field — OR on explicit "📍 Rileva zona" click —
 *      calls Nominatim, fills hidden fields and shows a friendly chip.
 *   3. The form submits to the existing `createTerritory` server action
 *      with the hidden bbox_ fields populated — no server-side changes.
 *   4. If Nominatim fails, an error message appears and a collapsible
 *      "Inserisci manualmente" section lets the user type coordinates.
 */

import { useEffect, useState } from 'react';

import { cn } from '@/lib/utils';
import { createTerritory } from '@/app/(dashboard)/territories/_actions';

// ------------------------------------------------------------------ types

type TerritoryType = 'cap' | 'comune' | 'provincia' | 'regione';

interface BboxResult {
  ne_lat: number;
  ne_lng: number;
  sw_lat: number;
  sw_lng: number;
  displayName: string;
}

// ------------------------------------------------------------------ config

const TYPE_LABEL: Record<TerritoryType, string> = {
  cap: 'CAP',
  comune: 'Comune',
  provincia: 'Provincia',
  regione: 'Regione',
};

const TYPE_PLACEHOLDER: Record<TerritoryType, { code: string; name: string }> = {
  cap:       { code: '80100',    name: 'Napoli centro' },
  comune:    { code: 'Napoli',   name: 'Napoli' },
  provincia: { code: 'NA',       name: 'Provincia di Napoli' },
  regione:   { code: 'Campania', name: 'Campania' },
};

// ------------------------------------------------------------------ Nominatim

async function resolvebbox(
  type: TerritoryType,
  code: string,
): Promise<BboxResult> {
  const q = buildQuery(type, code.trim());
  const url = `https://nominatim.openstreetmap.org/search?${new URLSearchParams({
    q,
    format: 'json',
    limit: '1',
    countrycodes: 'it',
    addressdetails: '0',
  })}`;

  const res = await fetch(url, {
    headers: {
      'Accept-Language': 'it',
      'User-Agent': 'SolarLead/1.0 (support@solarlead.it)',
    },
  });
  if (!res.ok) throw new Error(`Errore rete Nominatim: HTTP ${res.status}`);

  const data: Array<{
    boundingbox: [string, string, string, string];
    display_name: string;
  }> = await res.json();

  if (!data.length)
    throw new Error(
      'Nessuna zona trovata per questo codice. Controlla il valore e riprova.',
    );

  // data.length > 0 guaranteed above
  // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
  const first = data[0]!;
  const bb = first.boundingbox;
  return {
    sw_lat: Number(bb[0]),
    ne_lat: Number(bb[1]),
    sw_lng: Number(bb[2]),
    ne_lng: Number(bb[3]),
    displayName: first.display_name.split(',').slice(0, 3).join(', '),
  };
}

function buildQuery(type: TerritoryType, code: string): string {
  switch (type) {
    case 'cap':
      return `${code} Italia`;
    case 'comune':
      return `${code}, Italia`;
    case 'provincia':
      return `Provincia di ${code}, Italia`;
    case 'regione':
      return `${code}, Italia`;
  }
}

// ------------------------------------------------------------------ styles

const INPUT_CLASS =
  'w-full rounded-lg bg-surface-container-low px-3 py-2 text-sm text-on-surface ' +
  'placeholder:text-on-surface-variant/60 outline-none transition-colors ' +
  'focus:bg-surface-container-high focus:ring-2 focus:ring-primary/30';

// ------------------------------------------------------------------ component

export function TerritoryAddForm() {
  const [type, setType] = useState<TerritoryType>('cap');
  const [code, setCode] = useState('');
  const [bbox, setBbox] = useState<BboxResult | null>(null);
  const [resolving, setResolving] = useState(false);
  const [resolveError, setResolveError] = useState<string | null>(null);

  const ex = TYPE_PLACEHOLDER[type];

  // Clear bbox/error when type or code changes
  useEffect(() => {
    setBbox(null);
    setResolveError(null);
  }, [type, code]);

  async function handleResolve() {
    if (!code.trim()) {
      setResolveError('Inserisci prima il codice del territorio.');
      return;
    }
    setResolving(true);
    setResolveError(null);
    setBbox(null);
    try {
      const result = await resolvebbox(type, code);
      setBbox(result);
    } catch (e) {
      setResolveError((e as Error).message);
    } finally {
      setResolving(false);
    }
  }

  function handleCodeBlur() {
    if (code.trim().length >= 2 && !bbox && !resolving) {
      void handleResolve();
    }
  }

  return (
    <form action={createTerritory} className="flex flex-col gap-4">
      {/* Header */}
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Nuovo territorio
        </p>
        <p className="mt-1 text-xs text-on-surface-variant">
          Inserisci tipo e codice — le coordinate vengono rilevate
          automaticamente via OpenStreetMap.
        </p>
      </div>

      {/* Type */}
      <label className="flex flex-col gap-1.5">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Tipo
        </span>
        <select
          name="type"
          value={type}
          onChange={(e) => setType(e.target.value as TerritoryType)}
          required
          className={INPUT_CLASS}
        >
          {(Object.keys(TYPE_LABEL) as TerritoryType[]).map((t) => (
            <option key={t} value={t}>
              {TYPE_LABEL[t]}
            </option>
          ))}
        </select>
      </label>

      {/* Code — auto-resolves on blur */}
      <label className="flex flex-col gap-1.5">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Codice
        </span>
        <input
          name="code"
          required
          maxLength={32}
          placeholder={ex.code}
          value={code}
          onChange={(e) => setCode(e.target.value)}
          onBlur={handleCodeBlur}
          className={INPUT_CLASS}
        />
        <span className="text-[11px] text-on-surface-variant">
          CAP 5 cifre · sigla provincia · nome comune o regione
        </span>
      </label>

      {/* Name */}
      <label className="flex flex-col gap-1.5">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Nome
        </span>
        <input
          name="name"
          required
          maxLength={128}
          placeholder={ex.name}
          className={INPUT_CLASS}
        />
      </label>

      {/* Priority */}
      <label className="flex flex-col gap-1.5">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Priorità
        </span>
        <input
          name="priority"
          type="number"
          min={1}
          max={10}
          defaultValue={5}
          className={INPUT_CLASS}
        />
        <span className="text-[11px] text-on-surface-variant">
          1 = bassa · 10 = massima
        </span>
      </label>

      {/* ---- Area geografica ---- */}
      <div className="rounded-xl border border-outline-variant/20 bg-surface-container-low p-3 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Area geografica
            </p>
            <p className="mt-0.5 text-[11px] text-on-surface-variant">
              Necessaria per avviare la scansione tetti.
            </p>
          </div>
          <button
            type="button"
            disabled={resolving || !code.trim()}
            onClick={() => void handleResolve()}
            className={cn(
              'shrink-0 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors',
              resolving
                ? 'cursor-wait bg-surface-container text-on-surface-variant opacity-60'
                : !code.trim()
                  ? 'cursor-not-allowed bg-surface-container text-on-surface-variant opacity-40'
                  : 'bg-primary text-on-primary hover:bg-primary/90',
            )}
          >
            {resolving ? '⏳ Ricerca…' : '📍 Rileva zona'}
          </button>
        </div>

        {/* Stato: trovata */}
        {bbox && (
          <div className="flex items-start gap-2.5 rounded-lg bg-primary-container/20 border border-primary/10 px-3 py-2.5">
            <span className="text-base shrink-0">✅</span>
            <div className="min-w-0">
              <p className="text-xs font-semibold text-on-surface">
                Zona trovata
              </p>
              <p className="mt-0.5 text-[11px] text-on-surface-variant leading-snug">
                {bbox.displayName}
              </p>
              <p className="mt-1 font-mono text-[10px] text-on-surface-variant/60">
                N {bbox.ne_lat.toFixed(4)}° E {bbox.ne_lng.toFixed(4)}° ·{' '}
                S {bbox.sw_lat.toFixed(4)}° E {bbox.sw_lng.toFixed(4)}°
              </p>
            </div>
          </div>
        )}

        {/* Stato: errore */}
        {resolveError && !bbox && (
          <div className="flex items-start gap-2.5 rounded-lg bg-error-container/20 border border-error/10 px-3 py-2.5">
            <span className="text-base shrink-0">⚠️</span>
            <div>
              <p className="text-xs font-semibold text-on-surface">
                Rilevamento fallito
              </p>
              <p className="text-[11px] text-on-surface-variant mt-0.5">
                {resolveError}
              </p>
              <p className="mt-1.5 text-[11px] text-on-surface-variant">
                Usa il form qui sotto oppure trova le coordinate su{' '}
                <a
                  href="https://boundingbox.klokantech.com"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-semibold text-primary hover:underline"
                >
                  boundingbox.klokantech.com
                </a>
                .
              </p>
            </div>
          </div>
        )}

        {/* Stato: iniziale */}
        {!bbox && !resolveError && !resolving && (
          <p className="text-[11px] text-on-surface-variant italic">
            Premi Tab dopo il codice o clicca &ldquo;📍 Rileva zona&rdquo; — le
            coordinate vengono trovate in automatico.
          </p>
        )}

        {/* Hidden fields letti dal server action */}
        <input type="hidden" name="bbox_ne_lat" value={bbox?.ne_lat ?? ''} />
        <input type="hidden" name="bbox_ne_lng" value={bbox?.ne_lng ?? ''} />
        <input type="hidden" name="bbox_sw_lat" value={bbox?.sw_lat ?? ''} />
        <input type="hidden" name="bbox_sw_lng" value={bbox?.sw_lng ?? ''} />

        {/* Override manuale (collassato) */}
        <details className="group">
          <summary className="cursor-pointer select-none list-none text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant hover:text-on-surface">
            <span className="group-open:hidden">▸ Coordinate manuali</span>
            <span className="hidden group-open:inline">▾ Coordinate manuali</span>
          </summary>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <ManualCoord
              label="NE Lat"
              placeholder={bbox?.ne_lat.toFixed(4) ?? '40.855'}
              onCommit={(v) => setBbox((b) => b ? { ...b, ne_lat: v } : mkManual({ ne_lat: v }))}
            />
            <ManualCoord
              label="NE Lng"
              placeholder={bbox?.ne_lng.toFixed(4) ?? '14.270'}
              onCommit={(v) => setBbox((b) => b ? { ...b, ne_lng: v } : mkManual({ ne_lng: v }))}
            />
            <ManualCoord
              label="SW Lat"
              placeholder={bbox?.sw_lat.toFixed(4) ?? '40.835'}
              onCommit={(v) => setBbox((b) => b ? { ...b, sw_lat: v } : mkManual({ sw_lat: v }))}
            />
            <ManualCoord
              label="SW Lng"
              placeholder={bbox?.sw_lng.toFixed(4) ?? '14.240'}
              onCommit={(v) => setBbox((b) => b ? { ...b, sw_lng: v } : mkManual({ sw_lng: v }))}
            />
          </div>
        </details>
      </div>

      {/* Excluded */}
      <label className="flex items-center gap-2 text-xs text-on-surface-variant cursor-pointer">
        <input
          name="excluded"
          type="checkbox"
          className="h-4 w-4 rounded border-surface-container-highest accent-primary"
        />
        Escludi dalla scansione automatica
      </label>

      <button
        type="submit"
        className="w-full rounded-xl bg-primary py-2.5 text-sm font-semibold text-on-primary transition-opacity hover:opacity-90"
      >
        Aggiungi territorio
      </button>
    </form>
  );
}

// ------------------------------------------------------------------ utils

function mkManual(partial: Partial<BboxResult>): BboxResult {
  return {
    ne_lat: 0,
    ne_lng: 0,
    sw_lat: 0,
    sw_lng: 0,
    displayName: 'manuale',
    ...partial,
  };
}

function ManualCoord({
  label,
  placeholder,
  onCommit,
}: {
  label: string;
  placeholder: string;
  onCommit: (v: number) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] text-on-surface-variant">{label}</span>
      <input
        type="number"
        step="any"
        placeholder={placeholder}
        onBlur={(e) => {
          const v = parseFloat(e.target.value);
          if (!isNaN(v)) onCommit(v);
        }}
        className={cn(
          INPUT_CLASS,
          'text-xs',
        )}
      />
    </label>
  );
}
