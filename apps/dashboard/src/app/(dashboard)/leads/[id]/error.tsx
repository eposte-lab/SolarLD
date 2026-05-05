'use client';

/**
 * Route segment error boundary for /leads/[id].
 *
 * Lifts the actual error message + stack out of the production "digest only"
 * masking, so when a server render fails (PostgREST schema drift, missing
 * column on subjects/roofs, etc.) we can read the real cause directly in
 * the dashboard instead of having to dig through Vercel logs.
 *
 * Triage workflow:
 *   1. Open the lead → this boundary catches the throw and shows
 *      `error.message` + `error.digest` + `error.stack`.
 *   2. Fix the code, click "Riprova".
 *
 * Once the funnel v3 ↔ dashboard schema mismatch settles, this can be
 * thinned back to a generic 'Qualcosa è andato storto' page.
 */

import { useEffect } from 'react';

export default function LeadDetailError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surface to browser console too — useful when copying logs.
    console.error('lead-detail-error', error);
  }, [error]);

  return (
    <div className="space-y-4 p-6">
      <header>
        <h1 className="font-headline text-2xl font-bold tracking-tighter">
          Errore caricamento lead
        </h1>
        <p className="text-sm text-on-surface-variant">
          La pagina di dettaglio non è riuscita a renderizzare. Dettagli sotto
          per debug; clicca &quot;Riprova&quot; quando il fix è in produzione.
        </p>
      </header>

      <div className="space-y-2 rounded-lg bg-error-container/40 p-4 text-sm">
        <div>
          <span className="font-semibold">Messaggio:</span>{' '}
          <code className="rounded bg-surface-container-low px-1.5 py-0.5 font-mono text-xs">
            {error.message || '(nessun messaggio — probabile errore già loggato)'}
          </code>
        </div>
        {error.digest && (
          <div>
            <span className="font-semibold">Digest:</span>{' '}
            <code className="rounded bg-surface-container-low px-1.5 py-0.5 font-mono text-xs">
              {error.digest}
            </code>
          </div>
        )}
        {error.stack && (
          <details>
            <summary className="cursor-pointer font-semibold">Stack trace</summary>
            <pre className="mt-2 overflow-x-auto rounded bg-surface-container-low p-3 text-[11px] leading-snug">
              {error.stack}
            </pre>
          </details>
        )}
      </div>

      <button
        onClick={() => reset()}
        className="inline-flex items-center gap-2 rounded-full bg-primary px-4 py-2 text-sm font-semibold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-90"
      >
        Riprova
      </button>
    </div>
  );
}
