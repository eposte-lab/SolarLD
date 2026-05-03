/**
 * Territorio — FLUSSO 1 v3 mapping dashboard.
 *
 * Differs from the legacy `/territories` (plural):
 *   * v2 territories: bbox-based Atoka scan triggers
 *   * v3 territorio: geocentric OSM zone mapping → Places discovery
 *
 * Layout:
 *   1. Status banner: "Mappato X zone in Y settori il <data>"
 *   2. Action: "Rimappa il territorio" → POST /v1/territory/map
 *   3. Filtered table: zones with sector + province + score
 *
 * The mapping is async (5-15 min). The page polls /v1/territory/status
 * client-side via a separate component (or simple page-refresh on
 * action button click).
 */

import { redirect } from 'next/navigation';

import { TerritorioActions } from '@/components/territorio-actions';
import { TerritoryZonesTable } from '@/components/territorio-zones-table';
import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import {
  getTerritoryStatus,
  listTargetZones,
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
  let loadError: string | null = null;
  try {
    status = await getTerritoryStatus();
    zones = await listTargetZones({ limit: 500 });
  } catch (err) {
    loadError = err instanceof Error ? err.message : 'load_failed';
  }

  const sectorsLabelled = status.sectors_covered.map(
    (s) => SECTOR_LABELS[s] ?? s,
  );

  return (
    <main className="mx-auto flex w-full max-w-7xl flex-col gap-6 p-6">
      <header className="space-y-2">
        <p className="text-xs uppercase tracking-wider text-on-surface-variant">
          FLUSSO 1 — Geocentrico v3
        </p>
        <h1 className="text-3xl font-bold text-on-surface">
          Territorio mappato
        </h1>
        <p className="max-w-3xl text-sm text-on-surface-variant">
          La mappatura OSM identifica le zone target del tenant
          (industriali, commerciali, ricettive, agricole) sulla base dei
          settori selezionati in onboarding. Le zone sono poi usate dalla
          discovery Places quotidiana per scoprire candidati con
          coordinate precise del capannone.
        </p>
      </header>

      {loadError ? (
        <div className="rounded-md border border-error/40 bg-error/10 p-4 text-sm text-error">
          Errore nel caricamento: {loadError}
        </div>
      ) : null}

      <BentoGrid>
        <BentoCard>
          <div className="flex flex-col gap-3">
            <p className="text-xs uppercase tracking-wider text-on-surface-variant">
              Zone mappate
            </p>
            <p className="text-4xl font-bold tabular-nums text-on-surface">
              {status.zone_count.toLocaleString('it-IT')}
            </p>
            <p className="text-xs text-on-surface-variant">
              Ultima mappatura: {fmtDate(status.last_mapped_at)}
            </p>
          </div>
        </BentoCard>
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
        <BentoCard>
          <div className="flex flex-col gap-3">
            <p className="text-xs uppercase tracking-wider text-on-surface-variant">
              Azioni
            </p>
            <TerritorioActions />
            <p className="text-xs text-on-surface-variant">
              La rimappatura impiega 5-15 minuti per provincia. Non
              bloccare la pagina: il job continua in background.
            </p>
          </div>
        </BentoCard>
      </BentoGrid>

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
