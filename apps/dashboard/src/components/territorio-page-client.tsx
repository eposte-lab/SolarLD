'use client';

/**
 * TerritorioPageClient — wrapper client della pagina /territorio.
 *
 * Tiene lo state delle scan_jobs (lista a destra) e fornisce la
 * funzione `refresh` al ScanJobCreator (sx) e ScanJobsQueue (dx).
 * Polling ogni 30s per vedere il counter giornaliero aggiornarsi
 * mentre il worker macina lead.
 */

import { useCallback, useEffect, useState } from 'react';

import { ScanJobCreator } from '@/components/scan-job-creator';
import { ScanJobsQueue } from '@/components/scan-jobs-queue';
import { listScanJobs, type ScanJob } from '@/lib/data/scan-jobs';

export function TerritorioPageClient({
  initialJobs,
  maxDailyCap,
}: {
  initialJobs: ScanJob[];
  maxDailyCap: number;
}) {
  const [jobs, setJobs] = useState<ScanJob[]>(initialJobs);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const next = await listScanJobs();
      setJobs(next);
    } finally {
      setRefreshing(false);
    }
  }, []);

  // Auto-refresh ogni 30s per vedere il progress del worker
  useEffect(() => {
    const tid = setInterval(() => {
      void refresh();
    }, 30_000);
    return () => clearInterval(tid);
  }, [refresh]);

  return (
    <div className="grid gap-6 md:grid-cols-[minmax(280px,360px)_1fr]">
      <ScanJobCreator onCreated={refresh} maxDailyCap={maxDailyCap} />
      <div className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="font-headline text-lg font-bold tracking-tighter">
            Le tue scansioni{jobs.length > 0 ? ` (${jobs.length})` : ''}
          </h2>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={refreshing}
            className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant hover:text-on-surface disabled:opacity-50"
          >
            {refreshing ? 'Aggiorno…' : '↻ Aggiorna'}
          </button>
        </div>
        <ScanJobsQueue jobs={jobs} onMutate={refresh} />
      </div>
    </div>
  );
}
