/**
 * Campaign overrides — client-side data helpers.
 *
 * Overrides are time-boxed JSONB patches scoped to an acquisition campaign.
 * All mutations go through the FastAPI (`/v1/acquisition-campaigns/:id/overrides`).
 */

import { api } from '@/lib/api-client';
import type { CampaignOverrideRow, CampaignOverrideType } from '@/types/db';

export interface CampaignOverrideCreateInput {
  label?: string;
  override_type?: CampaignOverrideType;
  start_at: string;   // ISO-8601 datetime string (UTC)
  end_at: string;
  patch: Record<string, unknown>;
  experiment_id?: string;
}

/** List all overrides for a campaign. Pass activeOnly=true for current ones. */
export async function listCampaignOverrides(
  campaignId: string,
  activeOnly = false,
): Promise<CampaignOverrideRow[]> {
  const qs = activeOnly ? '?active_only=true' : '';
  const res = await api.get<{ overrides: CampaignOverrideRow[] }>(
    `/v1/acquisition-campaigns/${campaignId}/overrides${qs}`,
  );
  return res.overrides;
}

/** Create a new override for a campaign. */
export async function createCampaignOverride(
  campaignId: string,
  input: CampaignOverrideCreateInput,
): Promise<CampaignOverrideRow> {
  return api.post<CampaignOverrideRow>(
    `/v1/acquisition-campaigns/${campaignId}/overrides`,
    input,
  );
}

/** Hard-delete an override (safe: no downstream FKs). */
export async function deleteCampaignOverride(
  campaignId: string,
  overrideId: string,
): Promise<void> {
  await api.delete(`/v1/acquisition-campaigns/${campaignId}/overrides/${overrideId}`);
}

/** Create or update a campaign via PATCH. Used by CampaignConfigEditor. */
export async function patchAcquisitionCampaign(
  campaignId: string,
  body: Record<string, unknown>,
): Promise<void> {
  await api.patch(`/v1/acquisition-campaigns/${campaignId}`, body);
}
