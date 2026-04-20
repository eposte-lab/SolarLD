'use client';

import { useEffect } from 'react';
import { GradientButton } from '@/components/ui/gradient-button';

export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error('[DashboardError]', error);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-6 text-center">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-secondary">
          Errore pagina
        </p>
        <h2 className="mt-2 font-headline text-3xl font-bold tracking-tighter text-on-surface">
          Qualcosa è andato storto
        </h2>
        <p className="mt-2 max-w-md text-sm text-on-surface-variant">
          {error.message || 'Errore imprevisto. Prova a ricaricare la pagina.'}
        </p>
        {error.digest && (
          <p className="mt-1 font-mono text-xs text-on-surface-variant">
            digest: {error.digest}
          </p>
        )}
      </div>
      <GradientButton variant="primary" size="md" onClick={reset}>
        Riprova
      </GradientButton>
    </div>
  );
}
