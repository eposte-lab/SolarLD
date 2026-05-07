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

// ---------------------------------------------------------------------------
// Geocentric autopilot (auto-prepare + leads listing + render/send + reset)
// ---------------------------------------------------------------------------

export interface AutoPrepareResponse {
  job_id: string;
  tenant_id: string;
  enqueued_map: boolean;
  enqueued_funnel: boolean;
  zone_count: number;
  candidate_count: number;
  note: string;
}

export interface TerritoryLead {
  id: string;
  business_name: string | null;
  decision_maker_email: string | null;
  decision_maker_phone: string | null;
  sede_operativa_address: string | null;
  score: number | null;
  score_tier: string | null;
  pipeline_status: string | null;
  rendering_gif_url: string | null;
  rendering_image_url: string | null;
  outreach_sent_at: string | null;
  public_slug: string | null;
  created_at: string | null;
}

export interface TerritoryLeadsResponse {
  tenant_id: string;
  target_total: number;
  lead_count: number;
  cap_reached: boolean;
  leads: TerritoryLead[];
}

export interface ResetPipelineResponse {
  tenant_id: string;
  candidates_deleted: number;
  leads_deleted: number;
  cost_logs_deleted: number;
}

/** Idempotently kick off L0 (if needed) + the full L1→L6 funnel. */
export async function autoPrepareTerritory(): Promise<AutoPrepareResponse> {
  return apiFetch<AutoPrepareResponse>('/v1/territory/auto-prepare', {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

/** Funnel-v3 leads (capped at the geocentric target) for the autopilot UI. */
export async function getTerritoryLeads(): Promise<TerritoryLeadsResponse> {
  return apiFetch<TerritoryLeadsResponse>('/v1/territory/leads');
}

/** Trigger Creative Agent rendering (GIF/video) for a single lead. */
export async function regenerateLeadRendering(
  leadId: string,
): Promise<{ ok: boolean; lead_id: string }> {
  return apiFetch(`/v1/leads/${leadId}/regenerate-rendering`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

/** Trigger Outreach Agent send for a single lead. */
export async function sendLeadOutreach(
  leadId: string,
): Promise<{ ok: boolean; lead_id: string }> {
  return apiFetch(`/v1/leads/${leadId}/send-outreach`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

/** Wipe v3 pipeline state for the current tenant (zones survive). */
export async function resetTerritoryPipeline(): Promise<ResetPipelineResponse> {
  return apiFetch<ResetPipelineResponse>('/v1/territory/reset', {
    method: 'POST',
    body: JSON.stringify({}),
  });
}
