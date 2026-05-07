/**
 * Territorio — FLUSSO 1 v3 (geocentric autopilot UX).
 *
 * The page is intentionally minimal: configuration, OSM L0 mapping, and the
 * full L1→L6 funnel all run automatically on first visit (see
 * TerritorioAutopilot client component). The operator only interacts with
 * the resulting funnel-v3 leads, manually triggering GIF rendering and
 * outreach send per row.
 */

import { redirect } from 'next/navigation';

import { TerritorioAutopilot } from '@/components/territorio-autopilot';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { getTerritoryLeads } from '@/lib/data/territory-server';
import type { TerritoryLeadsResponse } from '@/lib/data/territory';

export const dynamic = 'force-dynamic';

export default async function TerritorioPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  let initialData: TerritoryLeadsResponse | null = null;
  try {
    initialData = await getTerritoryLeads();
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
          La pipeline completa (L0 mappatura zone, L1→L6 discovery + scraping +
          qualità + Solar API + scoring AI + creazione lead) gira sola in
          background, fino a 10 lead. Tu approvi singolarmente la generazione
          GIF e l’invio email per ogni contatto.
        </p>
      </header>

      <TerritorioAutopilot initialData={initialData} />
    </main>
  );
}
