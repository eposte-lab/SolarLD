'use client';

/**
 * Root error boundary — catches unhandled exceptions from server
 * components and displays a readable message instead of a blank page.
 */

import { useEffect } from 'react';

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error('[GlobalError]', error);
  }, [error]);

  return (
    <html lang="it">
      <body className="flex min-h-screen items-center justify-center bg-[#f4f7f6] p-8 font-sans">
        <div className="w-full max-w-lg rounded-xl bg-white p-8 shadow-md">
          <p className="text-xs font-semibold uppercase tracking-widest text-[#b22200]">
            Errore applicazione
          </p>
          <h1 className="mt-2 text-2xl font-bold text-[#2b2f2f]">
            Qualcosa è andato storto
          </h1>
          <pre className="mt-4 max-h-60 overflow-auto rounded-lg bg-[#f4f7f6] p-4 text-xs text-[#2b2f2f] whitespace-pre-wrap">
            {error.message || 'Errore sconosciuto'}
            {error.digest ? `\n\nDigest: ${error.digest}` : ''}
          </pre>
          <button
            onClick={reset}
            className="mt-6 rounded-full bg-[#006a37] px-5 py-2.5 text-sm font-semibold text-white hover:opacity-90"
          >
            Riprova
          </button>
        </div>
      </body>
    </html>
  );
}
