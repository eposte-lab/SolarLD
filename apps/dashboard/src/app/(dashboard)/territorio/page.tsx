/**
 * Territorio — refactor totale (sprint scan_jobs).
 *
 * Pagina mono-funzione:
 *   Sinistra: form per creare una nuova scansione (territorio + settori + cap)
 *   Destra:   lista delle scansioni del tenant ordinate per priority,
 *             drag-drop per riordinare, azioni rapide pausa/rilancia/archivia
 *
 * Le zone OSM (tenant_target_areas) restano dettaglio implementativo
 * interno del funnel L0, NON sono più mostrate qui.
 */

import { redirect } from 'next/navigation';

import { TerritorioPageClient } from '@/components/territorio-page-client';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { listScanJobsServer } from '@/lib/data/territory-server';
import type { ScanJob } from '@/lib/data/scan-jobs';

export const dynamic = 'force-dynamic';

export default async function TerritorioPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  let initialJobs: ScanJob[] = [];
  let loadError: string | null = null;
  try {
    initialJobs = await listScanJobsServer();
  } catch (err) {
    loadError = err instanceof Error ? err.message : 'load_failed';
  }

  return (
    <main className="mx-auto flex w-full max-w-7xl flex-col gap-6 p-6">
      <header className="space-y-2">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Territorio
        </p>
        <h1 className="font-headline text-3xl font-bold tracking-tighter text-on-surface md:text-4xl">
          Trova lead per territorio
        </h1>
        <p className="max-w-3xl text-sm text-on-surface-variant">
          Imposta a sinistra <strong>dove</strong> cercare (regione/provincia/comune)
          e <strong>cosa</strong> (settori), e quanti contatti vuoi al massimo
          al giorno. La scansione parte subito e appare a destra. Quando
          raggiunge il limite giornaliero si ferma e riprende il giorno
          successivo. Se trascini una lista sopra un&apos;altra, viene
          consumata per prima.
        </p>
      </header>

      {loadError && (
        <div className="rounded-md border border-error/40 bg-error/10 p-4 text-sm text-error">
          Errore nel caricamento: {loadError}
        </div>
      )}

      <TerritorioPageClient
        initialJobs={initialJobs}
        maxDailyCap={ctx.tenant.max_daily_validated_cap ?? 5000}
      />
    </main>
  );
}
