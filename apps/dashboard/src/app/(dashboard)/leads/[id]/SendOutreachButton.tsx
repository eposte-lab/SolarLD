'use client';

/**
 * Client-side button to trigger POST /v1/leads/:id/send-outreach.
 *
 * Idempotent on the backend side (deterministic job_id), but we also
 * guard against double-click with a local state machine.
 */

import { Check, Send, X } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { api, ApiError } from '@/lib/api-client';

type State =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'success'; message: string }
  | { kind: 'error'; message: string };

interface Props {
  leadId: string;
  alreadySent: boolean;
}

export function SendOutreachButton({ leadId, alreadySent }: Props) {
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: 'idle' });

  async function onClick(force: boolean) {
    setState({ kind: 'sending' });
    try {
      await api.post(
        `/v1/leads/${leadId}/send-outreach?channel=email${force ? '&force=true' : ''}`,
        {},
      );
      setState({
        kind: 'success',
        message: force
          ? 'Re-invio in coda. Tra pochi secondi la pipeline avanzerà.'
          : 'Invio in coda. Tra pochi secondi la pipeline avanzerà.',
      });
      // Nudge the server components to re-render with the new state.
      setTimeout(() => router.refresh(), 2000);
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

  const busy = state.kind === 'sending';

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <button
          onClick={() => onClick(false)}
          disabled={busy || alreadySent}
          className="inline-flex items-center gap-2 rounded-full bg-gradient-primary px-6 py-3 text-sm font-bold text-on-primary shadow-ambient-sm transition-all hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-50 disabled:saturate-50"
          title={alreadySent ? 'Già inviato — usa "Re-invia" per forzare' : undefined}
        >
          {!busy && !alreadySent && (
            <Send size={14} strokeWidth={2.25} aria-hidden />
          )}
          {busy ? 'Invio in corso…' : alreadySent ? 'Outreach già inviato' : 'Invia outreach'}
        </button>
        {alreadySent && (
          <button
            onClick={() => onClick(true)}
            disabled={busy}
            className="rounded-full bg-surface-container-highest px-6 py-3 text-sm font-semibold text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
          >
            Re-invia (force)
          </button>
        )}
      </div>

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
