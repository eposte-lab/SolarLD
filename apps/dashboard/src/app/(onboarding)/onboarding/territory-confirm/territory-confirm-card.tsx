'use client';

/**
 * Client component for the territory-confirm card.
 *
 * Renders a summary of the tenant's territorial footprint (regioni,
 * province, CAP from sorgente + the per-territory rows) and gates the
 * confirm button behind a required acceptance checkbox. The "confirm"
 * button fires the `confirmTerritory` server action.
 *
 * Stays client-side so:
 *   - checkbox state can disable the button instantly
 *   - useTransition shows a pending state during the POST
 *
 * If the server action redirects away (success) the component unmounts
 * naturally; if it redirects to `?error=...` the query-string flash is
 * surfaced by reading it via useSearchParams.
 */

import { useState, useTransition } from 'react';
import { useSearchParams } from 'next/navigation';

import { cn } from '@/lib/utils';

import { confirmTerritory } from './_actions';

export interface TerritorySummary {
  id: string;
  type: 'cap' | 'comune' | 'provincia' | 'regione';
  code: string;
  name: string;
}

export interface TerritoryConfirmCardProps {
  regioni: string[];
  province: string[];
  cap: string[];
  territories: TerritorySummary[];
}

export function TerritoryConfirmCard({
  regioni,
  province,
  cap,
  territories,
}: TerritoryConfirmCardProps) {
  const [accepted, setAccepted] = useState(false);
  const [isPending, startTransition] = useTransition();
  const searchParams = useSearchParams();
  const errorKey = searchParams.get('error');

  const hasAny =
    regioni.length + province.length + cap.length + territories.length > 0;

  function handleConfirm() {
    startTransition(async () => {
      await confirmTerritory();
    });
  }

  return (
    <div className="space-y-6">
      {!hasAny && (
        <div className="rounded-xl bg-tertiary-container px-5 py-4 text-sm font-semibold text-on-tertiary-container">
          Non hai ancora definito una zona. Torna allo step <strong>Sorgente</strong> o aggiungi almeno un territorio dalla pagina <strong>Territori</strong>.
        </div>
      )}

      {errorKey && (
        <div
          role="alert"
          className="rounded-xl bg-error-container px-5 py-3 text-sm font-semibold text-on-error-container"
        >
          {ERROR_COPY[errorKey] ?? `Errore: ${errorKey}`}
        </div>
      )}

      <section className="rounded-2xl bg-surface-container-lowest p-6 shadow-ambient-sm">
        <h2 className="font-headline text-xl font-bold tracking-tight text-on-surface">
          La tua zona di esclusiva
        </h2>

        <dl className="mt-5 grid gap-4 sm:grid-cols-3">
          <SummaryBlock label="Regioni" items={regioni} empty="Nessuna" />
          <SummaryBlock label="Province" items={province} empty="Nessuna" />
          <SummaryBlock label="CAP" items={cap} empty="Nessuno" />
        </dl>

        {territories.length > 0 && (
          <>
            <h3 className="mt-6 text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Territori aggiunti ({territories.length})
            </h3>
            <ul className="mt-2 flex flex-wrap gap-2">
              {territories.map((t) => (
                <li
                  key={t.id}
                  className="rounded-full bg-surface-container px-3 py-1 text-xs text-on-surface"
                >
                  <span className="font-semibold">{t.name}</span>
                  <span className="ml-1 text-on-surface-variant">
                    ({TYPE_LABEL[t.type]} {t.code})
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section className="space-y-4">
        <label className="flex items-start gap-3 rounded-xl bg-surface-container-lowest p-4 shadow-ambient-sm">
          <input
            type="checkbox"
            checked={accepted}
            onChange={(e) => setAccepted(e.target.checked)}
            className="mt-1 h-4 w-4 accent-primary"
            aria-describedby="territory-confirm-hint"
          />
          <span className="flex-1 text-sm text-on-surface">
            <span className="block font-semibold">
              Confermo che questa è la mia zona di esclusiva.
            </span>
            <span
              id="territory-confirm-hint"
              className="mt-1 block text-on-surface-variant"
            >
              Comprendo che dopo la conferma potrò modificare i
              territori solo tramite il supporto SolarLead. ATECO,
              dimensioni aziendali e parametri commerciali restano
              liberamente modificabili.
            </span>
          </span>
        </label>

        <div className="flex items-center justify-end">
          <button
            type="button"
            onClick={handleConfirm}
            disabled={!accepted || !hasAny || isPending}
            className={cn(
              'rounded-xl bg-primary px-6 py-2.5 text-sm font-semibold text-on-primary shadow-ambient-sm transition-opacity',
              (!accepted || !hasAny || isPending) && 'opacity-40',
            )}
          >
            {isPending ? 'Conferma in corso…' : 'Confermo e blocco la mia zona'}
          </button>
        </div>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------

function SummaryBlock({
  label,
  items,
  empty,
}: {
  label: string;
  items: string[];
  empty: string;
}) {
  return (
    <div>
      <dt className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
        {label}
      </dt>
      <dd className="mt-1">
        {items.length === 0 ? (
          <span className="text-sm text-on-surface-variant/70">{empty}</span>
        ) : (
          <span className="flex flex-wrap gap-1.5">
            {items.map((v) => (
              <span
                key={v}
                className="rounded-md bg-primary-container px-2 py-0.5 text-xs font-semibold text-on-primary-container"
              >
                {v}
              </span>
            ))}
          </span>
        )}
      </dd>
    </div>
  );
}

const TYPE_LABEL: Record<TerritorySummary['type'], string> = {
  cap: 'CAP',
  comune: 'Comune',
  provincia: 'Provincia',
  regione: 'Regione',
};

const ERROR_COPY: Record<string, string> = {
  api_unreachable:
    'Impossibile raggiungere il server API. Riprova tra qualche secondo.',
  confirm_failed_401: 'Sessione scaduta — ricarica la pagina e riprova.',
  confirm_failed_403: 'Non hai il permesso di completare questa operazione.',
};
