/**
 * Compact table of mapped OSM zones for the /territorio page.
 *
 * Server component (no interactivity for MVP) — Sprint 5+ adds a
 * Leaflet map preview when we ship `tenant_target_areas.geometry`
 * fetching via /v1/territory/zones/{id}/geojson.
 */

import type { TargetZone } from '@/lib/data/territory';

interface Props {
  zones: TargetZone[];
  sectorLabels: Record<string, string>;
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

export function TerritoryZonesTable({ zones, sectorLabels }: Props) {
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
            <th className="px-3 py-2 text-left">Settori match</th>
            <th className="px-3 py-2 text-left">Provincia</th>
            <th className="px-3 py-2 text-right">Area</th>
            <th className="px-3 py-2 text-right">Score</th>
            <th className="px-3 py-2 text-left">Centroide</th>
            <th className="px-3 py-2 text-left">OSM</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-outline-variant bg-surface-container">
          {zones.map((z) => {
            const primary = z.primary_sector
              ? sectorLabels[z.primary_sector] ?? z.primary_sector
              : '—';
            const matched = z.matched_sectors.map(
              (s) => sectorLabels[s] ?? s,
            );
            const osmUrl = `https://www.openstreetmap.org/${z.osm_type}/${z.osm_id}`;
            const mapsUrl = `https://www.google.com/maps?q=${z.centroid_lat},${z.centroid_lng}`;
            return (
              <tr key={z.id} className="hover:bg-surface-container-high">
                <td className="px-3 py-2 font-semibold text-on-surface">
                  {primary}
                </td>
                <td className="px-3 py-2 text-on-surface-variant">
                  {matched.join(' · ')}
                </td>
                <td className="px-3 py-2 text-on-surface-variant">
                  {z.province_code ?? '—'}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-on-surface">
                  {fmtArea(z.area_m2)}
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
                    {z.centroid_lat.toFixed(4)}, {z.centroid_lng.toFixed(4)}
                  </a>
                </td>
                <td className="px-3 py-2 font-mono text-xs">
                  <a
                    href={osmUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary hover:underline"
                  >
                    {z.osm_type}/{z.osm_id}
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
