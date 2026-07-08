/**
 * Prospector — client-side bindings for the FastAPI /v1/prospector surface.
 *
 * v3 (Sprint maggio 2026): la ricerca usa Google Places, non più Atoka.
 * Le liste salvate possono essere convalidate per il fotovoltaico
 * (esegue L2-L4 funnel inline) e poi avviare outreach on-demand.
 */
import { api } from '@/lib/api-client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** A single Places hit returned by /v1/prospector/search. */
export interface ProspectorPlace {
  google_place_id: string;
  display_name: string | null;
  formatted_address: string | null;
  lat: number;
  lng: number;
  types: string[];
  business_status: string | null;
  user_ratings_total: number | null;
  rating: number | null;
  website: string | null;
  phone: string | null;
  google_maps_uri: string | null;
}

/** v3 search input. Settore + comune/provincia required at API level. */
export interface SearchInput {
  sector: string;
  province_code?: string;
  comune?: string;
  radius_km?: number;
  keyword?: string;
  limit?: number;
}

export interface SearchResponse {
  items: ProspectorPlace[];
  count: number;
  /** True when the API returned hand-curated placeholder data because
   *  the tenant is flagged `is_demo` and the OpenAPI.it token is not
   *  configured yet. UI surfaces a "Dati di esempio" banner. */
  is_demo_data?: boolean;
}

export type ValidationStatus =
  | 'pending'
  | 'validating'
  | 'accepted'
  | 'rejected'
  | 'no_building'
  | 'api_error'
  | 'skipped';

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
  // v3 lifecycle
  source: 'atoka' | 'places' | 'openapi_it' | null;
  validation_started_at: string | null;
  validation_completed_at: string | null;
  outreach_started_at: string | null;
  outreach_completed_at: string | null;
  /** 'solar_rooftop' (default) goes through L4 Solar gate.
   *  'generic_outreach' bypasses Solar — non-rooftop B2B campaign. */
  campaign_type: CampaignType | null;
  /** UUID of the email_templates row to use for outreach, or null. */
  email_template_id: string | null;
}

export interface ProspectListItem {
  id: string;
  list_id: string;
  tenant_id: string;
  vat_number: string | null;
  legal_name: string;
  hq_address: string | null;
  google_place_id: string | null;
  place_lat: number | null;
  place_lng: number | null;
  place_types: string[] | null;
  business_status: string | null;
  user_ratings_total: number | null;
  rating: number | null;
  website_domain: string | null;
  phone: string | null;
  google_maps_uri: string | null;
  validation_status: ValidationStatus;
  validated_at: string | null;
  scan_candidate_id: string | null;
  imported_subject_id: string | null;
  imported_at: string | null;
  created_at: string;
}

export type CampaignType = 'solar_rooftop' | 'generic_outreach';

export interface CreateListInput {
  name: string;
  description?: string;
  search_filter: Record<string, unknown>;
  items: ProspectorPlace[];
  /** Default 'solar_rooftop' goes through the L4 Solar gate as before.
   *  'generic_outreach' bypasses Solar — for non-rooftop campaigns
   *  (e.g. amministratori condominio service offerings). */
  campaign_type?: CampaignType;
}

export interface ValidateStatusResponse {
  list_id: string;
  started_at: string | null;
  completed_at: string | null;
  item_count: number;
  by_status: Record<string, number>;
}

export interface OutreachStatusResponse {
  list_id: string;
  started_at: string | null;
  completed_at: string | null;
  accepted_count: number;
}

// ---------------------------------------------------------------------------
// API surface
// ---------------------------------------------------------------------------

/** Available sectors (wizard_groups) for the form dropdown. */
export async function fetchSectors(): Promise<string[]> {
  const res = await api.get<{ sectors: string[] }>('/v1/prospector/presets');
  return res.sectors;
}

export async function searchProspector(input: SearchInput): Promise<SearchResponse> {
  return api.post<SearchResponse>('/v1/prospector/search', input);
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

// ---------------------------------------------------------------------------
// v3 — Validation + Outreach launch
// ---------------------------------------------------------------------------

export async function validateProspectList(
  listId: string,
): Promise<{ queued: boolean; job_id: string }> {
  return api.post(`/v1/prospector/lists/${listId}/validate`, {});
}

export async function getValidateStatus(
  listId: string,
): Promise<ValidateStatusResponse> {
  return api.get(`/v1/prospector/lists/${listId}/validate/status`);
}

export async function launchOutreachForList(
  listId: string,
): Promise<{ queued: boolean; job_id: string }> {
  return api.post(`/v1/prospector/lists/${listId}/launch-outreach`, {});
}

export async function getOutreachStatus(
  listId: string,
): Promise<OutreachStatusResponse> {
  return api.get(`/v1/prospector/lists/${listId}/outreach/status`);
}
