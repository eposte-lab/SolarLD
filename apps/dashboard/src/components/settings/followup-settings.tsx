'use client';

/**
 * FollowupSettings — lets the operator set a dedicated sender address
 * for follow-up emails, separate from the main outreach domain.
 *
 * Saves via PATCH /v1/tenants/me (the same endpoint used by BrandingEditor).
 * The address is validated only for basic format — the operator is
 * responsible for configuring SPF/DKIM on their end.
 */

import { useState } from 'react';

import { api, ApiError } from '@/lib/api-client';
import { cn } from '@/lib/utils';

interface Props {
  initialEmail: string | null;
}

export function FollowupSettings({ initialEmail }: Props) {
  const [value, setValue] = useState(initialEmail ?? '');
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  async function save() {
    setStatus('saving');
    setErrorMsg(null);
    try {
      await api.patch('/v1/tenants/me', {
        followup_from_email: value.trim() || null,
      });
      setStatus('saved');
      setTimeout(() => setStatus('idle'), 2500);
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError ? err.message : 'Errore nel salvataggio.',
      );
      setStatus('error');
    }
  }

  const isDirty = value.trim() !== (initialEmail ?? '');

  return (
    <div className="space-y-3">
      <div>
        <label
          htmlFor="followup-email"
          className="mb-1.5 block text-sm font-medium text-on-surface"
        >
          Indirizzo mittente follow-up
        </label>
        <p className="mb-2 text-xs text-on-surface-variant">
          Se impostato, tutti i follow-up manuali e automatici usano
          questo indirizzo invece di{' '}
          <span className="font-mono">outreach@{'{'}dominio{'}'}</span>.
          Accetta formato semplice{' '}
          <span className="font-mono">followup@azienda.it</span> oppure
          completo{' '}
          <span className="font-mono">Nome {'<'}followup@azienda.it{'>'}</span>.
        </p>
        <input
          id="followup-email"
          type="text"
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setStatus('idle');
            setErrorMsg(null);
          }}
          placeholder="followup@azienda.it"
          className={cn(
            'w-full rounded-lg border bg-surface-container-lowest px-3 py-2 text-sm text-on-surface',
            'placeholder-on-surface-variant/50 focus:outline-none',
            status === 'error'
              ? 'border-error/60 focus:border-error'
              : 'border-outline-variant/40 focus:border-primary/60',
          )}
        />
      </div>

      {errorMsg && (
        <p className="text-xs text-error">{errorMsg}</p>
      )}

      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={!isDirty || status === 'saving'}
          className={cn(
            'rounded-lg px-4 py-2 text-sm font-semibold transition-colors',
            'bg-primary text-on-primary hover:bg-primary/90',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
        >
          {status === 'saving' ? 'Salvo…' : 'Salva'}
        </button>

        {status === 'saved' && (
          <span className="text-sm text-primary">Salvato</span>
        )}

        {value.trim() && value.trim() !== (initialEmail ?? '') && (
          <button
            onClick={() => {
              setValue(initialEmail ?? '');
              setStatus('idle');
            }}
            className="text-sm text-on-surface-variant hover:text-on-surface hover:underline"
          >
            Annulla
          </button>
        )}
      </div>
    </div>
  );
}
