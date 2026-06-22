'use client';

/**
 * RepaintRenderingButton — the SECOND render button.
 *
 * Unlike "Rigenera rendering" (which re-derives everything from the Google
 * Solar API, and so silently does nothing while Solar billing is 403), this
 * re-paints ONLY the panels on the bare aerial already saved in Storage,
 * reusing the panel geometry from `roofs.derivations`. So it:
 *   - works even with Solar billing down (no Google call at all),
 *   - is cheap (a single Replicate paint),
 *   - lets the operator iterate on a bad paint straight from here.
 *
 * Backend: `POST /v1/leads/:id/repaint-rendering` → `repaint_task`.
 * Disabled when there is no existing render to paint on.
 */

import { Check, Paintbrush, X } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { api, ApiError } from '@/lib/api-client';

type State =
  | { kind: 'idle' }
  | { kind: 'queuing' }
  | { kind: 'success'; message: string }
  | { kind: 'error'; message: string };

/** Shares the per-lead regen cap (MAX_RENDERING_REGENERATIONS, apps/api). */
const MAX_REGEN = 100;

interface Props {
  leadId: string;
  /** Regenerations already consumed (shared budget with "Rigenera"). */
  regenCount: number;
  /** Whether a render already exists to repaint on. */
  hasRender: boolean;
}

export function RepaintRenderingButton({ leadId, regenCount, hasRender }: Props) {
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: 'idle' });

  const limitReached = regenCount >= MAX_REGEN;
  const disabled = !hasRender || limitReached;

  async function onClick() {
    if (disabled) return;
    setState({ kind: 'queuing' });
    try {
      await api.post(`/v1/leads/${leadId}/repaint-rendering`, {});
      setState({
        kind: 'success',
        message:
          'In coda. Ricostruisce foto + video (animazione) dall’aerea già salvata, senza Google Solar. Compare tra circa 3-4 minuti — la pagina si aggiorna da sola.',
      });
      // Paint (~30s) + Kling transition video (~1.5-3 min): refresh only once
      // it's plausibly done. An early refresh would cache the OLD media under
      // the new ?v= key (see the post-upload cache-bust in repaint_service).
      // Two attempts cover the spread of render durations.
      setTimeout(() => router.refresh(), 200000);
      setTimeout(() => router.refresh(), 330000);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? 'Errore di rete. Verifica la connessione e riprova.'
            : 'Errore sconosciuto. Riprova tra qualche minuto.';
      setState({ kind: 'error', message: msg });
    }
  }

  const busy = state.kind === 'queuing';

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={onClick}
        disabled={busy || disabled}
        title={
          !hasRender
            ? 'Nessun render esistente. Usa prima "Rigenera rendering".'
            : limitReached
              ? `Limite di ${MAX_REGEN} rigenerazioni raggiunto per questo lead`
              : 'Ridisegna solo i pannelli sull’aerea già salvata, senza richiamare Google Solar'
        }
        className="inline-flex items-center gap-2 rounded-lg bg-surface-container-highest px-4 py-2 text-xs font-semibold text-on-surface transition-colors hover:bg-surface-container-high disabled:cursor-not-allowed disabled:opacity-60"
      >
        <Paintbrush
          size={12}
          strokeWidth={2.25}
          className={busy ? 'animate-pulse' : ''}
          aria-hidden
        />
        {busy ? 'Ridipintura in coda…' : 'Ridipingi pannelli'}
      </button>
      <p className="text-[11px] text-on-surface-variant">
        Solo pannelli, sull&apos;aerea esistente · non serve Google Solar
      </p>
      {state.kind === 'success' && (
        <p className="inline-flex items-center gap-1.5 text-xs font-semibold text-primary">
          <Check size={12} strokeWidth={2.5} aria-hidden />
          {state.message}
        </p>
      )}
      {state.kind === 'error' && (
        <p className="inline-flex items-start gap-1.5 text-xs font-semibold text-error">
          <X size={12} strokeWidth={2.5} className="mt-0.5 shrink-0" aria-hidden />
          {state.message}
        </p>
      )}
    </div>
  );
}
