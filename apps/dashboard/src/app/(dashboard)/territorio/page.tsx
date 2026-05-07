/**
 * Territorio — FLUSSO 1 v3 (geocentric autopilot UX).
 *
 * The page is intentionally minimal: configuration, OSM L0 mapping, and the
 * L1→L3 scan all run automatically on first visit (see TerritorioAutopilot
 * client component). The operator only interacts with the candidate pool
 * and qualifies a handful of leads on demand.
 *
 * Settings (settori + province) remain editable from /settings; we no
 * longer surface the editor on this page to keep the autopilot UX clean.
 */

import { redirect } from 'next/navigation';

import { TerritorioAutopilot } from '@/components/territorio-autopilot';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { getScanResults } from '@/lib/data/territory-server';
import type { ScanResultsResponse } from '@/lib/data/territory';

export const dynamic = 'force-dynamic';

export default async function TerritorioPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  // Best-effort SSR snapshot so the panel renders without an empty flash.
  let initialData: ScanResultsResponse | null = null;
  try {
    initialData = await getScanResults();
  } catch {
    initialData = null;
  }

  return (
    <main className="mx-auto flex w-full max-w-7xl flex-col gap-6 p-6">
      <header className="space-y-2">
        <p className="text-xs uppercase tracking-wider text-on-surface-variant">
          FLUSSO 1 — Geocentrico v3
        </p>
        <h1 className="text-3xl font-bold text-on-surface">Contatti target</h1>
        <p className="max-w-3xl text-sm text-on-surface-variant">
          La preparazione (mappatura territorio + scoperta candidati) gira da
          sola in background senza costi API. Tu approvi singolarmente i
          candidati che vuoi qualificare con Solar API + scoring AI: il
          sistema crea i lead corrispondenti, fino a un massimo di 10
          contatti finali per evitare sprechi.
        </p>
      </header>

      <TerritorioAutopilot initialData={initialData} />
    </main>
  );
}
