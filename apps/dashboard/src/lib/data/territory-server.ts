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

// ---------------------------------------------------------------------------
// Zone metrics — Sprint 3b
// ---------------------------------------------------------------------------
//
// Per ogni `tenant_target_areas.id` aggrega:
//   - candidates: count(scan_candidates) WHERE roof_id IN (roofs WHERE
//                 territory_id = zone.id)
//   - leads:      count(leads) WHERE roof_id IN (...) AND engagement_score > 0
//   - schedule_status: 'active' se zone.id ∈ una scan_schedules attiva,
//                      'paused' se in una scan_schedules paused,
//                      'never' altrimenti
//
// Tre query Supabase, JS aggregation. Lightweight enough per il render
// della pagina /territorio (tenant typical: 10-200 zone).

import { createSupabaseServerClient } from '@/lib/supabase/server';

import type { ZoneMetrics } from '@/components/territorio-zones-table';

export async function listZoneMetrics(
  zoneIds: string[],
): Promise<Record<string, ZoneMetrics>> {
  if (zoneIds.length === 0) return {};
  const sb = await createSupabaseServerClient();

  // Step 1: roofs grouped by territory_id (only those in our zones)
  const { data: roofRows } = await sb
    .from('roofs')
    .select('id, territory_id')
    .in('territory_id', zoneIds);
  const roofsByZone = new Map<string, string[]>();
  for (const r of (roofRows ?? []) as Array<{ id: string; territory_id: string }>) {
    const arr = roofsByZone.get(r.territory_id) ?? [];
    arr.push(r.id);
    roofsByZone.set(r.territory_id, arr);
  }

  // Flatten all roof_ids for the next two queries
  const allRoofIds = Array.from(roofsByZone.values()).flat();
  const candidatesByRoof = new Map<string, number>();
  const leadsByRoof = new Map<string, number>();

  if (allRoofIds.length > 0) {
    // Step 2a: count scan_candidates per roof
    const { data: scRows } = await sb
      .from('scan_candidates')
      .select('roof_id')
      .in('roof_id', allRoofIds);
    for (const r of (scRows ?? []) as Array<{ roof_id: string }>) {
      candidatesByRoof.set(r.roof_id, (candidatesByRoof.get(r.roof_id) ?? 0) + 1);
    }

    // Step 2b: count leads with engagement > 0 per roof
    const { data: leadRows } = await sb
      .from('leads')
      .select('roof_id')
      .in('roof_id', allRoofIds)
      .gt('engagement_score', 0);
    for (const r of (leadRows ?? []) as Array<{ roof_id: string }>) {
      leadsByRoof.set(r.roof_id, (leadsByRoof.get(r.roof_id) ?? 0) + 1);
    }
  }

  // Step 3: scan_schedules grouped by territory_id
  const { data: scheduleRows } = await sb
    .from('scan_schedules')
    .select('territory_ids, status')
    .neq('status', 'archived');
  const zoneStatus = new Map<string, 'active' | 'paused' | 'never'>();
  for (const s of (scheduleRows ?? []) as Array<{
    territory_ids: string[] | null;
    status: 'active' | 'paused';
  }>) {
    const ids = s.territory_ids ?? [];
    // empty array = "tutte le zone del tenant" → covers all our zones
    const targetIds = ids.length > 0 ? ids : zoneIds;
    for (const zid of targetIds) {
      const prev = zoneStatus.get(zid);
      if (prev === 'active') continue; // already best
      if (s.status === 'active' || prev !== 'paused') {
        zoneStatus.set(zid, s.status);
      }
    }
  }

  // Step 4: aggregate per zone
  const out: Record<string, ZoneMetrics> = {};
  for (const zid of zoneIds) {
    const roofIds = roofsByZone.get(zid) ?? [];
    let candidates = 0;
    let leads = 0;
    for (const rid of roofIds) {
      candidates += candidatesByRoof.get(rid) ?? 0;
      leads += leadsByRoof.get(rid) ?? 0;
    }
    out[zid] = {
      candidates,
      leads,
      schedule_status: zoneStatus.get(zid) ?? 'never',
    };
  }
  return out;
}
