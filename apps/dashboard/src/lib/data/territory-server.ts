/**
 * Server-side territory reads (RSC only).
 *
 * Lives separately from `./territory.ts` because the latter is imported
 * by `'use client'` components (TerritorioActions, TerritorioConfig)
 * which mutates the territory. Mixing both into one module would drag
 * `next/headers` (via `api-client-server.ts`) into the browser bundle
 * and fail the build.
 */

import 'server-only';

import { apiFetchServer } from '../api-client-server';
import type {
  ScanResultsResponse,
  TargetZone,
  TerritoryStatus,
} from './territory';

/** Snapshot for the dashboard status banner. */
export async function getTerritoryStatus(): Promise<TerritoryStatus> {
  return apiFetchServer<TerritoryStatus>('/v1/territory/status');
}

/** Polygons for the map / list view. Sector / province filters optional. */
export async function listTargetZones(opts: {
  sector?: string;
  province?: string;
  limit?: number;
} = {}): Promise<TargetZone[]> {
  const params = new URLSearchParams();
  if (opts.sector) params.set('sector', opts.sector);
  if (opts.province) params.set('province', opts.province.toUpperCase());
  if (opts.limit) params.set('limit', String(opts.limit));
  const qs = params.toString();
  return apiFetchServer<TargetZone[]>(
    qs ? `/v1/territory/zones?${qs}` : '/v1/territory/zones',
  );
}

/** Latest v3 scan results for the current tenant. */
export async function getScanResults(): Promise<ScanResultsResponse> {
  return apiFetchServer<ScanResultsResponse>('/v1/territory/scan-results');
}
