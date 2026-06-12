'use client';

/**
 * ResendToAddressForm — production resend of a lead's OFFICIAL outreach to an
 * operator-supplied alternate address (e.g. the decision-maker asked for the
 * offer at a different email). Sends the IDENTICAL official email — same
 * template, plant data and rendering — only the To: header changes.
 *
 * Backend: POST /v1/leads/{id}/resend-to-address. Unlike the demo-only
 * SendTestOutreachForm it works for production tenants, but a `reason` is
 * mandatory and every send is recorded in `audit_log` (operator, lead,
 * address, reason) — so an alternate-recipient send is always traceable.
 */

import { Send, Check, X, Mail } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { api, ApiError } from '@/lib/api-client';
import { GradientButton } from '@/components/ui/gradient-button';

type State =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'success'; message: string }
  | { kind: 'error'; message: string };

interface Props {
  leadId: string;
}

export function ResendToAddressForm({ leadId }: Props) {
  const router = useRouter();
  const [override, setOverride] = useState('');
  const [reason, setReason] = useState('');
  const [state, setState] = useState<State>({ kind: 'idle' });

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const email = override.trim();
    const why = reason.trim();
    if (!email || why.length < 3) return;
    setState({ kind: 'sending' });
    try {
      await api.post(`/v1/leads/${leadId}/resend-to-address`, {
        recipient_override: email,
        reason: why,
      });
      setState({
        kind: 'success',
        message: `Outreach ufficiale reinviato a ${email}. Registrato nell'audit.`,
      });
      setTimeout(() => router.refresh(), 5000);
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

  const busy = state.kind === 'sending';
  const canSend = override.trim().length > 0 && reason.trim().length >= 3;

  return (
    <section className="rounded-2xl bg-surface-container-high p-5 ring-1 ring-outline-variant shadow-ambient">
      <div className="mb-4 flex items-start gap-3">
        <span
          aria-hidden
          className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/15 text-primary"
        >
          <Mail size={13} strokeWidth={2.25} />
        </span>
        <div>
          <p className="text-sm font-semibold text-on-surface">
            Reinvia a un altro indirizzo
          </p>
          <p className="mt-1 text-xs leading-relaxed text-on-surface-variant">
            Invia l&apos;outreach ufficiale <strong>identico</strong> (stesso
            template, dati impianto e rendering) a un indirizzo diverso — ad es.
            se il referente ha chiesto l&apos;offerta su un&apos;altra mail. Il
            motivo è obbligatorio e ogni invio viene registrato nell&apos;audit.
          </p>
        </div>
      </div>

      <form onSubmit={onSubmit} className="flex flex-col gap-2">
        <input
          type="email"
          required
          placeholder="indirizzo@esempio.it"
          value={override}
          onChange={(e) => setOverride(e.target.value)}
          disabled={busy}
          className="rounded-full border border-outline-variant bg-surface-container-low px-4 py-2 text-sm text-on-surface placeholder:text-on-surface-muted focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30 disabled:opacity-50"
        />
        <input
          type="text"
          required
          minLength={3}
          maxLength={500}
          placeholder="Motivo (es. richiesto dal referente)"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          disabled={busy}
          className="rounded-full border border-outline-variant bg-surface-container-low px-4 py-2 text-sm text-on-surface placeholder:text-on-surface-muted focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30 disabled:opacity-50"
        />
        <div>
          <GradientButton type="submit" disabled={busy || !canSend}>
            <Send size={14} strokeWidth={2.25} aria-hidden />
            {busy ? 'Invio in corso…' : 'Reinvia outreach'}
          </GradientButton>
        </div>
      </form>

      {state.kind === 'success' && (
        <p className="mt-3 inline-flex items-center gap-1.5 text-xs font-semibold text-primary">
          <Check size={12} strokeWidth={2.5} aria-hidden />
          {state.message}
        </p>
      )}
      {state.kind === 'error' && (
        <p className="mt-3 inline-flex items-start gap-1.5 text-xs font-semibold text-error">
          <X size={12} strokeWidth={2.5} className="mt-0.5 shrink-0" aria-hidden />
          {state.message}
        </p>
      )}
    </section>
  );
}
