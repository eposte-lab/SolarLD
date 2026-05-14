/**
 * Compact table of mapped OSM zones for the /territorio page.
 *
 * Sprint 3b: added 3 metric columns (candidati raccolti, lead generati,
 * stato scansione) + row cliccabili → /leads?territorio={id}. Le metriche
 * arrivano dal server-side fetch (vedi listZoneMetrics in territory-server.ts).
 */

import Link from 'next/link';

import type { TargetZone } from '@/lib/data/territory';

export interface ZoneMetrics {
  /** Candidates collected in scan_candidates whose roof is in this zone. */
  candidates: number;
  /** Leads with engagement_score > 0 sourced from this zone. */
  leads: number;
  /** 'active' / 'paused' / 'never' based on scan_schedules.territory_ids. */
  schedule_status: 'active' | 'paused' | 'never';
}

interface Props {
  zones: TargetZone[];
  sectorLabels: Record<string, string>;
  metricsById?: Record<string, ZoneMetrics>;
}

function fmtArea(area: number | null): string {
  if (area === null || area === undefined) return '—';
  if (area < 10_000) return `${Math.round(area)} m²`;
  if (area < 1_000_000) return `${(area / 10_000).toFixed(1)} ha`;
  return `${(area / 1_000_000).toFixed(2)} km²`;
}

function fmtScore(score: number | null): string {
  if (score === null || score === undefined) return '—';
  return `${score.toFixed(0)}/100`;
}

function StatusBadge({ status }: { status: 'active' | 'paused' | 'never' }) {
  if (status === 'active') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-primary-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-on-primary-container">
        <span className="h-1.5 w-1.5 rounded-full bg-primary" /> attiva
      </span>
    );
  }
  if (status === 'paused') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
        in pausa
      </span>
    );
  }
  return (
    <span className="text-[10px] uppercase tracking-widest text-on-surface-variant/60">
      mai
    </span>
  );
}

export function TerritoryZonesTable({ zones, sectorLabels, metricsById }: Props) {
  if (zones.length === 0) {
    return (
      <p className="rounded-md border border-outline-variant bg-surface-container/50 p-6 text-sm text-on-surface-variant">
        Nessuna zona mappata. Avvia la prima mappatura per scoprire le
        aree compatibili con i settori target del tenant.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-md border border-outline-variant">
      <table className="min-w-full divide-y divide-outline-variant text-sm">
        <thead className="bg-surface-container-high text-xs uppercase tracking-wider text-on-surface-variant">
          <tr>
            <th className="px-3 py-2 text-left">Settore primario</th>
            <th className="px-3 py-2 text-left">Provincia</th>
            <th className="px-3 py-2 text-right">Area</th>
            <th className="px-3 py-2 text-right">Candidati</th>
            <th className="px-3 py-2 text-right">Lead</th>
            <th className="px-3 py-2 text-left">Scansione</th>
            <th className="px-3 py-2 text-right">Score</th>
            <th className="px-3 py-2 text-left">Mappa</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-outline-variant bg-surface-container">
          {zones.map((z) => {
            const primary = z.primary_sector
              ? sectorLabels[z.primary_sector] ?? z.primary_sector
              : '—';
            const mapsUrl = `https://www.google.com/maps?q=${z.centroid_lat},${z.centroid_lng}`;
            const metrics = metricsById?.[z.id] ?? {
              candidates: 0,
              leads: 0,
              schedule_status: 'never' as const,
            };
            return (
              <tr key={z.id} className="hover:bg-surface-container-high">
                <td className="px-3 py-2 font-semibold text-on-surface">
                  <Link
                    href={`/leads?territorio=${encodeURIComponent(z.id)}`}
                    className="hover:text-primary hover:underline"
                  >
                    {primary}
                  </Link>
                </td>
                <td className="px-3 py-2 text-on-surface-variant">
                  {z.province_code ?? '—'}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-on-surface">
                  {fmtArea(z.area_m2)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {metrics.candidates > 0 ? (
                    <span className="font-semibold text-on-surface">
                      {metrics.candidates.toLocaleString('it-IT')}
                    </span>
                  ) : (
                    <span className="text-on-surface-variant">—</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {metrics.leads > 0 ? (
                    <Link
                      href={`/leads?territorio=${encodeURIComponent(z.id)}`}
                      className="font-semibold text-primary hover:underline"
                    >
                      {metrics.leads.toLocaleString('it-IT')}
                    </Link>
                  ) : (
                    <span className="text-on-surface-variant">—</span>
                  )}
                </td>
                <td className="px-3 py-2">
                  <StatusBadge status={metrics.schedule_status} />
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-on-surface">
                  {fmtScore(z.matching_score)}
                </td>
                <td className="px-3 py-2 font-mono text-xs">
                  <a
                    href={mapsUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary hover:underline"
                  >
                    {z.centroid_lat.toFixed(3)},{z.centroid_lng.toFixed(3)}
                  </a>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
