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

import { ArrowUpRight } from 'lucide-react';
import Link from 'next/link';

import { SectionEyebrow } from '@/components/ui/section-eyebrow';
import { getGeoLeads } from '@/lib/data/geo-analytics';
import type { ProvinceAggregate } from '@/lib/data/geo-analytics';
import { GeoRadarMapLoader } from './geo-radar-map-loader';

interface GeoRadarMapProps {
  className?: string;
}

export async function GeoRadarMap({ className }: GeoRadarMapProps) {
  let aggregates: ProvinceAggregate[] = [];
  let pins: Awaited<ReturnType<typeof getGeoLeads>>['pins'] = [];

  try {
    const res = await getGeoLeads();
    aggregates = res.aggregates;
    pins = res.pins;
  } catch {
    // Non-fatal — renders empty map
  }

  // Header counts based on geolocated ACTIVE leads (the precise pins).
  const totalLeads = pins.length;
  const hotProvinces = aggregates.filter((a) => a.hot > 0).length;

  return (
    <div className={className}>
      {/* Card header */}
      <div className="mb-3 flex items-end justify-between">
        <div className="space-y-1">
          <SectionEyebrow>Geo Radar · Live</SectionEyebrow>
          <h2 className="font-headline text-2xl font-bold tracking-tighter text-on-surface">
            Lead Map
          </h2>
        </div>
        {totalLeads > 0 && (
          <div className="flex gap-3 text-right">
            <div>
              <SectionEyebrow tone="dim">Province</SectionEyebrow>
              <p className="font-headline text-xl font-bold tabular-nums tracking-tightest text-on-surface">
                {aggregates.length}
              </p>
            </div>
            <div>
              <SectionEyebrow tone="dim">Lead</SectionEyebrow>
              <p className="font-headline text-xl font-bold tabular-nums tracking-tightest text-on-surface">
                {totalLeads}
              </p>
            </div>
            {hotProvinces > 0 && (
              <div>
                <SectionEyebrow tone="dim">Hot zone</SectionEyebrow>
                <p className="font-headline text-xl font-bold tabular-nums tracking-tightest text-primary editorial-glow">
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
            { color: '#22C55E', label: 'Firmato' },
            { color: '#16A34A', label: 'Appuntamento' },
            { color: '#F4A45C', label: 'Hot' },
            { color: '#8A9499', label: 'Inviato' },
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

      {/* Map container — fixed height with floating glass overlay.
          La mappa dell'Italia viene SEMPRE mostrata (centrata su [12.5,
          41.9], zoom 4.8) anche senza lead: con aggregates vuoti il
          client non disegna marker ma rende comunque la mappa. Quando
          non c'è ancora nessun lead con provincia mostriamo solo una
          nota fluttuante in basso, invece di nascondere la mappa. */}
      <div className="relative h-[460px] overflow-hidden rounded-2xl ghost-border-strong lg:h-[560px]">
        <GeoRadarMapLoader aggregates={aggregates} pins={pins} />
        {pins.length === 0 ? (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center p-4">
            <p className="pointer-events-auto rounded-full liquid-glass-sm px-4 py-2 text-xs text-on-surface-variant shadow-liquid-glass">
              Nessun lead con provincia ancora.{' '}
              <a href="/territorio" className="font-semibold text-primary hover:underline">
                Configura il territorio →
              </a>
            </p>
          </div>
        ) : (
          <>
            {hotProvinces > 0 && (
              <Link
                href="/leads"
                className="group absolute left-4 top-4 max-w-[240px] rounded-2xl liquid-glass-sm p-5 transition-all duration-300 hover:-translate-y-0.5 hover:shadow-liquid-glass relative overflow-hidden"
              >
                <span
                  className="pointer-events-none absolute inset-0 bg-glass-specular"
                  aria-hidden
                />
                <div className="relative">
                  <SectionEyebrow tone="mint">Lead caldi</SectionEyebrow>
                  <p className="mt-1.5 font-headline text-4xl font-bold tracking-tightest text-on-surface">
                    <span>{hotProvinces}</span>
                    <span className="hero-decimal text-2xl"> province</span>
                  </p>
                  <p className="mt-1.5 text-[11px] leading-snug text-on-surface-variant">
                    con concentrazione hot/appointment attiva
                  </p>
                  <span className="mt-3.5 inline-flex items-center gap-1.5 text-[11px] font-semibold text-primary">
                    Esplora
                    <ArrowUpRight
                      size={12}
                      strokeWidth={2.5}
                      className="transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5"
                      aria-hidden
                    />
                  </span>
                </div>
              </Link>
            )}
          </>
        )}
      </div>
    </div>
  );
}
