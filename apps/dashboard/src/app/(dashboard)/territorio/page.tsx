/**
 * Territorio — Sprint 7 refactor stile /scoperta.
 *
 * Layout: pannello sinistro per CREARE una programmazione di scansione +
 * pannello destro con la lista delle programmazioni attive e zone già
 * scannerizzate. Rimosse le sezioni "Configurazione territorio",
 * "Mappatura OSM", "Pipeline test" e tabella zone full — confondevano
 * l'operatore senza dare valore (vivono ora solo come admin-debug).
 *
 * Il cliente Total Trade ha chiesto: "scelta libera del territorio,
 * settori, vedere distribuzione 500/cap=200/3gg, lista cronologica
 * delle scansioni". Questa pagina lo esprime in un layout pulito.
 */

import { redirect } from 'next/navigation';

import { ScanSchedulesPanel } from '@/components/scan-schedules-panel';
import { ScanResultsPanel } from '@/components/scan-results-panel';
import { TerritoryZonesTable } from '@/components/territorio-zones-table';
import { BentoCard } from '@/components/ui/bento-card';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import {
  getScanResults,
  getTerritoryStatus,
  listTargetZones,
  listZoneMetrics,
} from '@/lib/data/territory-server';
import type {
  ScanResultsResponse,
  TargetZone,
  TerritoryStatus,
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
  healthcare_private: 'Sanitario privato',
  agricultural_intensive: 'Agricolo intensivo',
  automotive: 'Automotive',
  education: 'Istruzione',
  personal_services: 'Servizi alla persona',
  professional_offices: 'Studi professionali',
  horeca: 'HoReCa',
  amministratori_condominio: 'Amministratori di condominio',
};

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

  let zoneMetrics: Record<string, import('@/components/territorio-zones-table').ZoneMetrics> = {};
  try {
    zoneMetrics = await listZoneMetrics(zones.map((z) => z.id));
  } catch {
    zoneMetrics = {};
  }

  // Scan results are best-effort.
  try {
    scanResults = await getScanResults();
  } catch {
    scanResults = null;
  }

  return (
    <main className="mx-auto flex w-full max-w-7xl flex-col gap-6 p-6">
      {/* ---- Header ---- */}
      <header className="space-y-2">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Territorio
        </p>
        <h1 className="font-headline text-3xl font-bold tracking-tighter text-on-surface md:text-4xl">
          Programma le tue scansioni
        </h1>
        <p className="max-w-3xl text-sm text-on-surface-variant">
          Configura quali zone scansionare, quali settori cercare, con
          quale cadenza e quanto budget giornaliero. Il sistema distribuisce
          automaticamente le scansioni grandi su più giorni (es. 500 candidati
          con cap 200 → 3 giorni).
        </p>
      </header>

      {loadError ? (
        <div className="rounded-md border border-error/40 bg-error/10 p-4 text-sm text-error">
          Errore nel caricamento: {loadError}
        </div>
      ) : null}

      {/* ---- Layout 2-column: programmazioni a sinistra (creator + lista),
              telemetria zone+candidati a destra. ScanSchedulesPanel gestisce
              entrambi (form crea + lista in linea) nello stesso component. */}
      <ScanSchedulesPanel />

      {/* ---- Riepilogo zone scannerizzate + lead generati ---- */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-headline text-xl font-bold tracking-tighter text-on-surface">
            Zone scannerizzate
          </h2>
          <p className="text-xs text-on-surface-variant">
            {zones.length} {zones.length === 1 ? 'zona' : 'zone'} ·{' '}
            {status.sectors_covered.length} settori coperti
          </p>
        </div>
        <BentoCard padding="tight" span="full">
          <TerritoryZonesTable
            zones={zones}
            sectorLabels={SECTOR_LABELS}
            metricsById={zoneMetrics}
          />
        </BentoCard>
      </section>

      {/* ---- Detailed scan waterfall + recommended candidates (read-only) ---- */}
      {scanResults !== null && scanResults.top_candidates.length > 0 ? (
        <section className="space-y-3">
          <h2 className="font-headline text-xl font-bold tracking-tighter text-on-surface">
            Candidati raccomandati dall&apos;ultima scansione
          </h2>
          <ScanResultsPanel data={scanResults} />
        </section>
      ) : null}
    </main>
  );
}
