'use client';

/**
 * FindBetterContactButton — operator-triggered premium contact re-enrichment.
 *
 * Calls POST /v1/leads/{id}/find-better-contact (fire-and-forget). The worker
 * looks up a named decision-maker email for the company domain (within the
 * capped budget), validates it, and updates the lead's contact in place. We
 * refresh after ~30s so the operator sees the upgraded email + premium badge.
 */

import { Sparkles, Check, X } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { api, ApiError } from '@/lib/api-client';

type State =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'done'; message: string }
  | { kind: 'error'; message: string };

export function FindBetterContactButton({ leadId }: { leadId: string }) {
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: 'idle' });

  async function onClick() {
    setState({ kind: 'sending' });
    try {
      await api.post(`/v1/leads/${leadId}/find-better-contact`, {});
      setState({
        kind: 'done',
        message:
          'Ricerca avviata. Ricarica tra ~30s: se troviamo un contatto migliore, l’email del lead viene aggiornata.',
      });
      setTimeout(() => router.refresh(), 30000);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? 'Errore di rete. Riprova.'
            : 'Errore sconosciuto. Riprova.';
      setState({ kind: 'error', message: msg });
    }
  }

  const busy = state.kind === 'sending';

  return (
    <div className="flex flex-col items-start gap-1.5">
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        className="inline-flex items-center gap-1.5 rounded-full border border-outline-variant bg-surface-container-low px-4 py-2 text-sm font-medium text-on-surface transition-colors hover:border-primary disabled:opacity-50"
      >
        <Sparkles size={14} strokeWidth={2.25} aria-hidden />
        {busy ? 'Ricerca in corso…' : 'Trova contatto migliore'}
      </button>
      {state.kind === 'done' && (
        <p className="inline-flex items-start gap-1.5 text-xs font-semibold text-primary">
          <Check size={12} strokeWidth={2.5} className="mt-0.5 shrink-0" aria-hidden />
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
