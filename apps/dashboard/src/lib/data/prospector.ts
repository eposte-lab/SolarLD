/**
 * Prospector — client-side bindings for the FastAPI /v1/prospector surface.
 *
 * Lives in lib/data so feature components can do a one-line import without
 * worrying about auth headers or path prefixes. Pure types + thin wrappers
 * over `apiClient` — no React, no DOM.
 *
 * Why client-side instead of server data-loader (like contatti.ts):
 *   • The Atoka discovery search needs API_KEY rotation/budgeting that
 *     lives in the FastAPI process, not in the dashboard's Supabase JWT.
 *   • Saved-list reads could go via Supabase server client, but keeping
 *     all prospector traffic on one side avoids a split where create()
 *     uses the API and read() uses Supabase, which is a footgun for
 *     subtle RLS/latency mismatches.
 */
import { api } from '@/lib/api-client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** ATECO preset chip surfaced in the search form. */
export interface AtecoPreset {
  label: string;
  ateco_codes: string[];
  description: string;
}

/** Flat result row — shape mirrors `prospector_service._profile_to_dict`. */
export interface ProspectorItem {
  vat_number: string | null;
  legal_name: string | null;
  ateco_code: string | null;
  ateco_description: string | null;
  employees: number | null;
  revenue_eur: number | null;
  hq_address: string | null;
  hq_cap: string | null;
  hq_city: string | null;
  hq_province: string | null;
  hq_lat: number | null;
  hq_lng: number | null;
  website_domain: string | null;
  decision_maker_name: string | null;
  decision_maker_role: string | null;
  decision_maker_email: string | null;
  linkedin_url: string | null;
  raw?: Record<string, unknown>;
}

export interface SearchInput {
  ateco_codes: string[];
  province_code?: string;
  region_code?: string;
  employees_min?: number;
  employees_max?: number;
  revenue_min_eur?: number;
  revenue_max_eur?: number;
  keyword?: string;
  limit?: number;
  offset?: number;
  preset_code?: string;
}

export interface SearchResponse {
  items: ProspectorItem[];
  count: number;
  limit: number;
  offset: number;
  estimated_cost_eur: number;
  error?: string;
}

export interface ProspectList {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  search_filter: Record<string, unknown>;
  preset_code: string | null;
  item_count: number;
  imported_count: number;
  launched_campaign_id: string | null;
  launched_at: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProspectListItem {
  id: string;
  list_id: string;
  tenant_id: string;
  vat_number: string;
  legal_name: string;
  ateco_code: string | null;
  ateco_description: string | null;
  employees: number | null;
  revenue_eur: number | null;
  hq_address: string | null;
  hq_cap: string | null;
  hq_city: string | null;
  hq_province: string | null;
  hq_lat: number | null;
  hq_lng: number | null;
  website_domain: string | null;
  decision_maker_name: string | null;
  decision_maker_role: string | null;
  decision_maker_email: string | null;
  linkedin_url: string | null;
  imported_subject_id: string | null;
  imported_at: string | null;
  created_at: string;
}

export interface CreateListInput {
  name: string;
  description?: string;
  search_filter: Record<string, unknown>;
  preset_code?: string;
  items: ProspectorItem[];
}

// ---------------------------------------------------------------------------
// API surface
// ---------------------------------------------------------------------------

export async function fetchPresets(): Promise<Record<string, AtecoPreset>> {
  const res = await api.get<{ presets: Record<string, AtecoPreset> }>(
    '/v1/prospector/presets',
  );
  return res.presets;
}

export async function searchProspector(input: SearchInput): Promise<SearchResponse> {
  return api.post<SearchResponse>('/v1/prospector/search', input);
}

export async function estimateCost(recordCount: number): Promise<number> {
  const res = await api.get<{ record_count: number; estimated_cost_eur: number }>(
    `/v1/prospector/cost-estimate?record_count=${recordCount}`,
  );
  return res.estimated_cost_eur;
}

export async function createList(input: CreateListInput): Promise<ProspectList> {
  return api.post<ProspectList>('/v1/prospector/lists', input);
}

export async function listProspectLists(opts: {
  page?: number;
  page_size?: number;
} = {}): Promise<{ rows: ProspectList[]; total: number; page: number; page_size: number }> {
  const params = new URLSearchParams();
  if (opts.page) params.set('page', String(opts.page));
  if (opts.page_size) params.set('page_size', String(opts.page_size));
  const qs = params.toString();
  return api.get(`/v1/prospector/lists${qs ? `?${qs}` : ''}`);
}

export async function getProspectList(
  listId: string,
  opts: { page?: number; page_size?: number } = {},
): Promise<{
  list: ProspectList;
  items: ProspectListItem[];
  items_total: number;
  page: number;
  page_size: number;
}> {
  const params = new URLSearchParams();
  if (opts.page) params.set('page', String(opts.page));
  if (opts.page_size) params.set('page_size', String(opts.page_size));
  const qs = params.toString();
  return api.get(`/v1/prospector/lists/${listId}${qs ? `?${qs}` : ''}`);
}

export async function deleteProspectList(listId: string): Promise<void> {
  await api.delete(`/v1/prospector/lists/${listId}`);
}
