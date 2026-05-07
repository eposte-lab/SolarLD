'use client';

/**
 * SendTestOutreachForm — demo-only outreach send.
 *
 * Renders on the lead detail page when `tenant.outreach_blocked=true`.
 * The standard SendOutreachButton is hidden in that mode (the
 * kill-switch would catch it and record `blocked_demo:` anyway, leaving
 * the operator without any visible feedback). This form replaces it
 * with a form that takes the operator's own email as the recipient.
 *
 * Backend: POST /v1/leads/{id}/send-test-outreach which validates the
 * override, refuses if the override matches the lead's actual email,
 * and enqueues OutreachAgent with `recipient_override` set. The full
 * pipeline runs (template render, ROI numbers, GIF embed, A/B
 * accounting) — only the To: header changes.
 */

import { Send, Check, X } from 'lucide-react';
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
  defaultEmail?: string | null;
}

export function SendTestOutreachForm({ leadId, defaultEmail }: Props) {
  const router = useRouter();
  const [override, setOverride] = useState<string>(defaultEmail ?? '');
  const [state, setState] = useState<State>({ kind: 'idle' });

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const value = override.trim();
    if (!value) return;
    setState({ kind: 'sending' });
    try {
      await api.post(`/v1/leads/${leadId}/send-test-outreach`, {
        recipient_override: value,
      });
      setState({
        kind: 'success',
        message: `Email di test inviata a ${value}. Controlla la tua casella tra qualche secondo.`,
      });
      setTimeout(() => router.refresh(), 5000);
    } catch (err) {
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
    <section className="rounded-2xl bg-amber-50 p-5 ring-1 ring-amber-200 shadow-ambient">
      <div className="mb-4 flex items-start gap-3">
        <span
          aria-hidden
          className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-200 text-xs font-bold text-amber-900"
        >
          ★
        </span>
        <div>
          <p className="text-sm font-semibold text-amber-900">
            Account demo · invio email reali disattivato
          </p>
          <p className="mt-1 text-xs leading-relaxed text-amber-800">
            Su questo account il bottone &laquo;Invia email&raquo; standard è
            sospeso: nessuna email partirà mai verso l&apos;email reale del
            lead. Per provare il flusso, inserisci la tua email personale
            qui sotto — riceverai esattamente la stessa email che andrebbe
            al cliente, con il rendering, i numeri ROI e il link al portale.
          </p>
        </div>
      </div>

      <form onSubmit={onSubmit} className="flex flex-col gap-2 sm:flex-row">
        <input
          type="email"
          required
          placeholder="latuamail@esempio.it"
          value={override}
          onChange={(e) => setOverride(e.target.value)}
          disabled={busy}
          className="flex-1 rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant focus:border-amber-500 focus:outline-none focus:ring-2 focus:ring-amber-300 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={busy || !override.trim()}
          className="inline-flex items-center justify-center gap-2 rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white shadow-ambient-sm transition-colors hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <Send size={14} strokeWidth={2.25} aria-hidden />
          {busy ? 'Invio in corso…' : 'Invia email di test'}
        </button>
      </form>

      {state.kind === 'success' && (
        <p className="mt-3 inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-700">
          <Check size={12} strokeWidth={2.5} aria-hidden />
          {state.message}
        </p>
      )}
      {state.kind === 'error' && (
        <p className="mt-3 inline-flex items-start gap-1.5 text-xs font-semibold text-rose-700">
          <X size={12} strokeWidth={2.5} className="mt-0.5 shrink-0" aria-hidden />
          {state.message}
        </p>
      )}
    </section>
  );
}
