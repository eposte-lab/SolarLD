'use client';

/**
 * GeoRadarMapLoader — thin client wrapper that lazy-loads the Mapbox component.
 *
 * `ssr: false` is only allowed inside Client Components (Next.js App Router).
 * This file is marked 'use client' so that `dynamic(..., { ssr: false })` works.
 * The server component `geo-radar-map.tsx` imports this instead of calling
 * `dynamic` directly.
 */

import dynamic from 'next/dynamic';

import type { ProvinceAggregate } from '@/lib/data/geo-analytics';

const GeoRadarMapClient = dynamic(
  () =>
    import('./geo-radar-map.client').then((mod) => ({
      default: mod.GeoRadarMapClient,
    })),
  {
    ssr: false,
    loading: () => (
      <div className="h-full w-full animate-pulse rounded-xl bg-surface-container-high" />
    ),
  },
);

export interface GeoRadarMapLoaderProps {
  aggregates: ProvinceAggregate[];
}

export function GeoRadarMapLoader({ aggregates }: GeoRadarMapLoaderProps) {
  return <GeoRadarMapClient aggregates={aggregates} />;
}
