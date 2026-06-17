'use client';

/**
 * SolarLayoutLink — opens the "real Google Solar API panel layout" overlay for
 * a lead in a popup.
 *
 * This is the DETERMINISTIC layout: PV panels drawn at the exact positions the
 * Google Solar API computed (no AI), on the building's aerial — i.e. where the
 * panels can actually, physically go. It's the technical-truth counterpart to
 * the marketing AI render, so the operator can catch an inflated "paper" array
 * (kWp on paper that wouldn't really fit) before quoting.
 *
 * Generated on demand by `GET /v1/leads/:id/solar-layout` (returns a short-lived
 * signed URL to a cached PNG). Shown only for warm/hot leads (gated upstream in
 * the lead page), so the one-time base-imagery fetch is bounded. We use a native
 * <dialog> (the repo's modal convention — no shared Modal component) and load
 * the image lazily on first open.
 */

import { Map, X } from 'lucide-react';
import { useRef, useState } from 'react';

import { api, ApiError } from '@/lib/api-client';

type State =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; url: string }
  | { kind: 'error'; message: string };

export function SolarLayoutLink({ leadId }: { leadId: string }) {
  const ref = useRef<HTMLDialogElement | null>(null);
  const [state, setState] = useState<State>({ kind: 'idle' });

  async function open() {
    ref.current?.showModal();
    // Fetch once per mount; the signed URL is reused while the dialog lives.
    if (state.kind === 'ready' || state.kind === 'loading') return;
    setState({ kind: 'loading' });
    try {
      const res = await api.get<{ url: string }>(`/v1/leads/${leadId}/solar-layout`);
      setState({ kind: 'ready', url: res.url });
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? 'Errore di rete nel caricamento del layout.'
            : 'Errore sconosciuto.';
      setState({ kind: 'error', message: msg });
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={open}
        className="inline-flex items-center gap-2 rounded-lg bg-surface-container-highest px-4 py-2 text-xs font-semibold text-on-surface transition-colors hover:bg-surface-container-high"
        title="Mostra dove la Google Solar API posiziona realmente i pannelli sul tetto (layout deterministico, non l'immagine commerciale AI)"
      >
        <Map size={12} strokeWidth={2.25} aria-hidden />
        Apri layout reale Solar API
      </button>

      <dialog
        ref={ref}
        className="m-auto w-full max-w-3xl rounded-2xl bg-surface p-0 shadow-ambient-lg backdrop:bg-on-surface/50 backdrop:backdrop-blur-sm"
      >
        <div className="flex items-center justify-between border-b border-outline-variant px-5 py-3">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Verità tecnica
            </p>
            <h3 className="font-headline text-lg font-bold tracking-tight text-on-surface">
              Layout reale Google Solar API
            </h3>
          </div>
          <button
            type="button"
            aria-label="Chiudi"
            onClick={() => ref.current?.close()}
            className="flex h-8 w-8 items-center justify-center rounded-full text-on-surface-variant transition-colors hover:bg-surface-container-high"
          >
            <X size={18} strokeWidth={2.25} aria-hidden />
          </button>
        </div>

        <div className="p-5">
          <p className="mb-3 text-xs text-on-surface-variant">
            Pannelli disegnati nelle posizioni esatte calcolate dalla Solar API —
            la disposizione effettivamente piazzabile sul tetto, non il render
            commerciale. Confrontala con l&apos;immagine AI per verificare che la
            proposta non sia gonfiata.
          </p>

          {state.kind === 'idle' || state.kind === 'loading' ? (
            <div className="flex h-64 items-center justify-center rounded-xl bg-surface-container-low text-sm text-on-surface-variant">
              Generazione del layout in corso…
            </div>
          ) : null}

          {state.kind === 'ready' ? (
            <>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={state.url}
                alt="Layout reale dei pannelli — Google Solar API"
                className="w-full rounded-xl"
              />
              <a
                href={state.url}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-3 inline-block text-xs font-semibold text-primary hover:underline"
              >
                Apri in una nuova scheda ↗
              </a>
            </>
          ) : null}

          {state.kind === 'error' ? (
            <div className="rounded-xl bg-error-container px-4 py-3 text-sm text-on-error-container">
              {state.message}
            </div>
          ) : null}
        </div>
      </dialog>
    </>
  );
}
