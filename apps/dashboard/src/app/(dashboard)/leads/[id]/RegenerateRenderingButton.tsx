'use client';

/**
 * RegenerateRenderingButton — re-runs the Creative agent for a single lead.
 *
 * Why this lives next to the Solar API inspection panel: the operator
 * looks at the panel count / kWp / orientation that the Solar API
 * inferred, decides whether the AI-painted "after" frame is acceptable,
 * and if not — clicks here to force a fresh run. The backend route
 * (`POST /v1/leads/:id/regenerate-rendering`) defaults `force=true`,
 * which bypasses the creative agent's idempotency guard so the new
 * AI-paint pipeline (nano-banana → Kling) actually executes even when
 * a previous PIL-based render already populated `rendering_image_url`.
 *
 * UX is deliberately understated — this is an ops tool, not a primary
 * CTA. Single small button with status text below.
 */

import { Check, RefreshCw, X } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { api, ApiError } from '@/lib/api-client';

type State =
  | { kind: 'idle' }
  | { kind: 'queuing' }
  | { kind: 'success'; message: string }
  | { kind: 'error'; message: string };

interface Props {
  leadId: string;
}

export function RegenerateRenderingButton({ leadId }: Props) {
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: 'idle' });

  async function onClick() {
    setState({ kind: 'queuing' });
    try {
      // force=true is the route default but we pass it explicitly so the
      // intent is visible in network logs / tests.
      await api.post(
        `/v1/leads/${leadId}/regenerate-rendering?force=true`,
        {},
      );
      setState({
        kind: 'success',
        message:
          'Rigenerazione in coda. Il sistema rifà l\u2019analisi del tetto e il render. Aggiorna la pagina tra circa 30 secondi.',
      });
      // Soft-refresh after the typical happy-path duration so the new
      // rendering shows up without a manual reload.
      setTimeout(() => router.refresh(), 30000);
    } catch (err) {
      // ApiError.message is already a sanitized Italian string from
      // api-client.ts — never JSON.stringify the body into the toast.
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? "Errore di rete. Verifica la connessione e riprova."
            : "Errore sconosciuto. Riprova tra qualche minuto.";
      setState({ kind: 'error', message: msg });
    }
  }

  const busy = state.kind === 'queuing';

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        className="inline-flex items-center gap-2 rounded-lg bg-surface-container-highest px-4 py-2 text-xs font-semibold text-on-surface transition-colors hover:bg-surface-container-high disabled:cursor-not-allowed disabled:opacity-60"
      >
        <RefreshCw
          size={12}
          strokeWidth={2.25}
          className={busy ? 'animate-spin' : ''}
          aria-hidden
        />
        {busy ? 'Rigenerazione in coda…' : 'Rigenera rendering'}
      </button>
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
