/**
 * Territory mapping data layer (FLUSSO 1 v3).
 *
 * Wraps the new `/v1/territory/*` endpoints introduced in Sprint 1.6:
 *   * GET  /v1/territory/status    → mapping snapshot (zone count, sectors)
 *   * GET  /v1/territory/zones     → list of mapped polygons
 *   * POST /v1/territory/map       → kicks off (re-)mapping job
 *
 * Distinct from the legacy `/v1/territories` (plural) which is the v2
 * Atoka-based scan endpoint. The new singular path drives the geocentric
 * v3 funnel.
 */

import { apiFetch } from '../api-client';

export interface TerritoryStatus {
  tenant_id: string;
  zone_count: number;
  sectors_covered: string[];
  last_mapped_at: string | null;
}

export interface TargetZone {
  id: string;
  osm_id: number;
  osm_type: 'way' | 'relation';
  centroid_lat: number;
  centroid_lng: number;
  area_m2: number | null;
  matched_sectors: string[];
  primary_sector: string | null;
  matching_score: number | null;
  province_code: string | null;
  status: 'active' | 'archived' | 'review';
}

export interface MapTerritoryResponse {
  job_id: string;
  tenant_id: string;
  wizard_groups: string[];
  province_codes: string[];
}

/** Snapshot for the dashboard status banner. */
export async function getTerritoryStatus(): Promise<TerritoryStatus> {
  return apiFetch<TerritoryStatus>('/v1/territory/status');
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
  return apiFetch<TargetZone[]>(
    qs ? `/v1/territory/zones?${qs}` : '/v1/territory/zones',
  );
}

/** Trigger a (re-)mapping run. Returns immediately with the ARQ job id. */
export async function mapTerritory(opts: {
  wizard_groups?: string[];
  province_codes?: string[];
} = {}): Promise<MapTerritoryResponse> {
  return apiFetch<MapTerritoryResponse>('/v1/territory/map', {
    method: 'POST',
    body: JSON.stringify(opts),
  });
}

// ---------------------------------------------------------------------------
// v3 Funnel manual trigger + scan results
// ---------------------------------------------------------------------------

export interface RunFunnelResponse {
  job_id: string;
  tenant_id: string;
  zone_count: number;
  max_l1_candidates: number;
}

export interface ScanStageSummary {
  l1_candidates: number;
  l2_with_email: number;
  l3_accepted: number;
  l4_solar_accepted: number;
  l5_recommended: number;
  /** Lead pipeline counts (downstream of L6 promotion). */
  l6_leads_created: number;
  leads_with_rendering: number;
  leads_outreach_sent: number;
  total_cost_eur: number;
  started_at: string | null;
  completed_at: string | null;
  /** True while the scan is in flight (started, not yet completed). */
  is_running: boolean;
}

export interface ScanCandidate {
  id: string;
  google_place_id: string | null;
  business_name: string | null;
  predicted_sector: string | null;
  stage: number;
  building_quality_score: number | null;
  solar_verdict: string | null;
  overall_score: number | null;
  recommended_for_rendering: boolean;
  lat: number | null;
  lng: number | null;
  website: string | null;
  phone: string | null;
  best_email: string | null;
  created_at: string;
}

export interface ScanResultsResponse {
  summary: ScanStageSummary;
  top_candidates: ScanCandidate[];
  scan_id: string | null;
}

/** Manually trigger the L1→L5 funnel for the current tenant.
 *  Requires at least one active zone in tenant_target_areas (L0 done). */
export async function runFunnelManual(opts: {
  max_l1_candidates?: number;
} = {}): Promise<RunFunnelResponse> {
  return apiFetch<RunFunnelResponse>('/v1/territory/run-funnel', {
    method: 'POST',
    body: JSON.stringify({ max_l1_candidates: opts.max_l1_candidates ?? 100 }),
  });
}

/** Latest v3 scan results for the current tenant. */
export async function getScanResults(): Promise<ScanResultsResponse> {
  return apiFetch<ScanResultsResponse>('/v1/territory/scan-results');
}
