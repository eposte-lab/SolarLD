/**
 * Territorio — FLUSSO 1 v3 mapping + scan dashboard.
 *
 * Layout:
 *   1. Header + descrizione
 *   2. BentoGrid:
 *      a. Zone mappate (count + ultima mappatura)
 *      b. Settori coperti (badges)
 *      c. Azioni (Rimappa L0 + Avvia scansione L1→L5)
 *   3. Zone OSM table
 *   4. Risultati ultima scansione (waterfall L1→L5 + candidati raccomandati)
 */

import { redirect } from 'next/navigation';

import { ScanResultsPanel } from '@/components/scan-results-panel';
import { TerritorioActions } from '@/components/territorio-actions';
import { TerritorioConfig } from '@/components/territorio-config';
import { TerritoryZonesTable } from '@/components/territorio-zones-table';
import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import {
  getScanResults,
  getTerritoryStatus,
  listTargetZones,
  type ScanResultsResponse,
  type TargetZone,
  type TerritoryStatus,
} from '@/lib/data/territory';

export const dynamic = 'force-dynamic';

const SECTOR_LABELS: Record<string, string> = {
  industry_heavy: 'Manifatturiero pesante',
  industry_light: 'Manifatturiero leggero',
  food_production: 'Produzione alimentare',
  logistics: 'Logistica',
  retail_gdo: 'Grande distribuzione',
  hospitality_large: 'Ricettivo grande',
  hospitality_food_service: 'Ristorazione collettiva',
  healthcare: 'Sanitario',
  agricultural_intensive: 'Agricolo intensivo',
  automotive: 'Automotive',
  education: 'Istruzione',
  personal_services: 'Servizi alla persona',
  professional_offices: 'Studi professionali',
  horeca: 'HoReCa',
};

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString('it-IT', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export default async function TerritorioPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  let status: TerritoryStatus = {
    tenant_id: ctx.tenant.id,
    zone_count: 0,
    sectors_covered: [],
    last_mapped_at: null,
  };
  let zones: TargetZone[] = [];
  let scanResults: ScanResultsResponse | null = null;
  let loadError: string | null = null;

  try {
    [status, zones] = await Promise.all([
      getTerritoryStatus(),
      listTargetZones({ limit: 500 }),
    ]);
  } catch (err) {
    loadError = err instanceof Error ? err.message : 'load_failed';
  }

  // Scan results are best-effort — don't fail the page if the endpoint
  // returns an error (e.g. no scan_cost_log rows yet).
  try {
    scanResults = await getScanResults();
  } catch {
    scanResults = null;
  }

  const sectorsLabelled = status.sectors_covered.map(
    (s) => SECTOR_LABELS[s] ?? s,
  );

  return (
    <main className="mx-auto flex w-full max-w-7xl flex-col gap-8 p-6">
      {/* ---- Header ---- */}
      <header className="space-y-2">
        <p className="text-xs uppercase tracking-wider text-on-surface-variant">
          FLUSSO 1 — Geocentrico v3
        </p>
        <h1 className="text-3xl font-bold text-on-surface">
          Territorio &amp; Scansione
        </h1>
        <p className="max-w-3xl text-sm text-on-surface-variant">
          <strong>L0</strong>: mappa le zone OSM compatibili con i settori
          scelti in onboarding. <strong>L1→L5</strong>: scopre aziende
          target via Google Places, scraping, filtro qualità edificio,
          Solar API e scoring Haiku — senza Atoka.
        </p>
      </header>

      {loadError ? (
        <div className="rounded-md border border-error/40 bg-error/10 p-4 text-sm text-error">
          Errore nel caricamento: {loadError}
        </div>
      ) : null}

      {/* ---- Config panel (wizard_groups + province) ---- */}
      <TerritorioConfig />

      {/* ---- BentoGrid: status + actions ---- */}
      <BentoGrid>
        {/* Zone count */}
        <BentoCard>
          <div className="flex flex-col gap-3">
            <p className="text-xs uppercase tracking-wider text-on-surface-variant">
              Zone OSM mappate
            </p>
            <p className="text-4xl font-bold tabular-nums text-on-surface">
              {status.zone_count.toLocaleString('it-IT')}
            </p>
            <p className="text-xs text-on-surface-variant">
              Ultima mappatura: {fmtDate(status.last_mapped_at)}
            </p>
          </div>
        </BentoCard>

        {/* Sectors */}
        <BentoCard>
          <div className="flex flex-col gap-3">
            <p className="text-xs uppercase tracking-wider text-on-surface-variant">
              Settori coperti
            </p>
            {sectorsLabelled.length === 0 ? (
              <p className="text-sm text-on-surface-variant">
                Nessuno — avvia la prima mappatura.
              </p>
            ) : (
              <ul className="flex flex-wrap gap-2">
                {sectorsLabelled.map((s) => (
                  <li
                    key={s}
                    className="rounded-full bg-surface-container-high px-3 py-1 text-xs font-semibold text-on-surface"
                  >
                    {s}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </BentoCard>

        {/* Actions */}
        <BentoCard>
          <div className="flex flex-col gap-3">
            <p className="text-xs uppercase tracking-wider text-on-surface-variant">
              Azioni
            </p>
            <TerritorioActions />
          </div>
        </BentoCard>
      </BentoGrid>

      {/* ---- Scan results waterfall ---- */}
      {scanResults !== null ? (
        <section className="space-y-3">
          <h2 className="text-xl font-semibold text-on-surface">
            Risultati scansione v3
          </h2>
          <ScanResultsPanel data={scanResults} />
        </section>
      ) : status.zone_count > 0 ? (
        <section className="rounded-md border border-outline-variant bg-surface-container/50 p-6">
          <p className="text-sm text-on-surface-variant">
            Zone mappate presenti. Premi{' '}
            <strong>Avvia scansione v3</strong> per lanciare il funnel
            L1→L5 e vedere i candidati raccomandati.
          </p>
        </section>
      ) : null}

      {/* ---- Zone OSM table ---- */}
      <section className="space-y-3">
        <h2 className="text-xl font-semibold text-on-surface">
          Zone OSM ({zones.length})
        </h2>
        <TerritoryZonesTable
          zones={zones}
          sectorLabels={SECTOR_LABELS}
        />
      </section>
    </main>
  );
}
