'use client';

/**
 * FollowupTrigger — one-click button to fire the engagement follow-up
 * cron for the calling tenant right now.
 *
 * Calls POST /v1/followup/trigger and shows the result:
 *   - how many follow-ups were queued
 *   - how many were skipped (cooldown / not eligible)
 */

import { useState } from 'react';

import { api, ApiError } from '@/lib/api-client';
import { cn } from '@/lib/utils';

interface Props {
  eligibleCount: number;
}

interface TriggerResult {
  ok: boolean;
  queued: number;
  skipped: number;
  message: string;
}

export function FollowupTrigger({ eligibleCount }: Props) {
  const [status, setStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
  const [result, setResult] = useState<TriggerResult | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  async function trigger() {
    setStatus('running');
    setResult(null);
    setErrorMsg(null);
    try {
      const res = await api.post<TriggerResult>('/v1/followup/trigger', {});
      setResult(res);
      setStatus('done');
    } catch (err) {
      // ApiError.message is already sanitized Italian copy from
      // api-client.ts — don't prefix with the HTTP code (the user
      // can't act on it).
      setErrorMsg(
        err instanceof ApiError
          ? err.message
          : 'Errore inatteso durante il trigger. Riprova tra qualche minuto.',
      );
      setStatus('error');
    }
  }

  if (status === 'done' && result) {
    return (
      <div className="space-y-3">
        <div className="rounded-lg bg-primary-container/40 px-4 py-3 text-sm text-on-primary-container">
          <p className="font-semibold">Valutazione completata</p>
          <p className="mt-1 text-on-primary-container/80">{result.message}</p>
        </div>
        <button
          onClick={() => { setStatus('idle'); setResult(null); }}
          className="text-sm text-on-surface-variant hover:text-on-surface hover:underline"
        >
          Resetta
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {errorMsg && (
        <div className="rounded-lg bg-error-container/40 px-4 py-3 text-sm text-on-error-container">
          <p>{errorMsg}</p>
          <button
            onClick={() => { setStatus('idle'); setErrorMsg(null); }}
            className="mt-1 font-semibold underline hover:no-underline"
          >
            Riprova
          </button>
        </div>
      )}

      <button
        onClick={trigger}
        disabled={status === 'running' || eligibleCount === 0}
        className={cn(
          'inline-flex items-center gap-2 rounded-lg px-5 py-2.5 text-sm font-semibold',
          'bg-primary text-on-primary shadow-ambient-sm transition-colors',
          'hover:bg-primary/90',
          'disabled:cursor-not-allowed disabled:opacity-50',
        )}
      >
        {status === 'running' ? (
          <>
            <SpinnerIcon />
            Valutazione in corso…
          </>
        ) : (
          'Avvia follow-up automatico ora'
        )}
      </button>

      {eligibleCount === 0 && (
        <p className="text-xs text-on-surface-variant">
          Nessun lead idoneo al momento (nessuno ha ricevuto l&apos;outreach iniziale
          o tutti sono in stati terminali).
        </p>
      )}
    </div>
  );
}

function SpinnerIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="16" height="16"
      viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true"
      className="animate-spin"
    >
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  );
}
