/**
 * GeoRadarMap — server wrapper for the Mapbox GL lead radar map.
 *
 * Fetches geo data server-side (RLS-scoped), then passes province
 * aggregates to GeoRadarMapLoader (a client component that does
 * `dynamic(..., { ssr: false })` — which is only allowed in client
 * components in the Next.js App Router).
 *
 * Falls back gracefully when NEXT_PUBLIC_MAPBOX_TOKEN is not set.
 */

import { getGeoLeads } from '@/lib/data/geo-analytics';
import type { ProvinceAggregate } from '@/lib/data/geo-analytics';
import { GeoRadarMapLoader } from './geo-radar-map-loader';

interface GeoRadarMapProps {
  className?: string;
}

export async function GeoRadarMap({ className }: GeoRadarMapProps) {
  let aggregates: ProvinceAggregate[] = [];

  try {
    const { aggregates: agg } = await getGeoLeads();
    aggregates = agg;
  } catch {
    // Non-fatal — renders empty map
  }

  const totalLeads = aggregates.reduce((s, a) => s + a.total, 0);
  const hotProvinces = aggregates.filter((a) => a.hot > 0).length;

  return (
    <div className={className}>
      {/* Card header */}
      <div className="mb-3 flex items-end justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Geo Radar · Live
          </p>
          <h2 className="font-headline text-2xl font-bold tracking-tighter">
            Lead Map
          </h2>
        </div>
        {totalLeads > 0 && (
          <div className="flex gap-3 text-right">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Province
              </p>
              <p className="font-headline text-xl font-bold tabular-nums text-primary">
                {aggregates.length}
              </p>
            </div>
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Lead
              </p>
              <p className="font-headline text-xl font-bold tabular-nums text-on-surface">
                {totalLeads}
              </p>
            </div>
            {hotProvinces > 0 && (
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  Hot zone
                </p>
                <p className="font-headline text-xl font-bold tabular-nums text-[#1a73e8]">
                  {hotProvinces}
                </p>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Legend */}
      {totalLeads > 0 && (
        <div className="mb-3 flex flex-wrap gap-3">
          {[
            { color: '#6afea0', label: 'Firmato' },
            { color: '#fdbb31', label: 'Appuntamento' },
            { color: '#1a73e8', label: 'Hot' },
            { color: '#aaaead', label: 'Inviato' },
          ].map(({ color, label }) => (
            <span key={label} className="flex items-center gap-1.5 text-[10px] text-on-surface-variant">
              <span
                className="inline-block h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: color }}
              />
              {label}
            </span>
          ))}
        </div>
      )}

      {/* Map container — fixed height */}
      <div className="h-[320px] overflow-hidden rounded-xl">
        {aggregates.length === 0 ? (
          <div className="flex h-full items-center justify-center rounded-xl bg-surface-container-low">
            <p className="text-sm text-on-surface-variant">
              Nessun lead con provincia ancora.{' '}
              <a href="/territories" className="font-semibold text-primary hover:underline">
                Connetti un territorio →
              </a>
            </p>
          </div>
        ) : (
          <GeoRadarMapLoader aggregates={aggregates} />
        )}
      </div>
    </div>
  );
}
