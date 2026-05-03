/**
 * On-demand LinkedIn enrichment (FLUSSO 1 v3, Sprint 4.3).
 *
 * Wraps POST /v1/leads/{id}/enrich/linkedin which calls Proxycurl
 * server-side. Cache TTL 60 days on subjects.linkedin_data.
 */

import { apiFetch } from '../api-client';

export interface LinkedInEnrichment {
  lead_id: string;
  subject_id: string;
  found: boolean;
  cache_hit: boolean;
  linkedin_url: string | null;
  name: string | null;
  description: string | null;
  employee_count_range: string | null;
  industry: string | null;
  founded_year: number | null;
  hq_country: string | null;
  hq_city: string | null;
  website: string | null;
}

export async function enrichLeadLinkedIn(
  leadId: string,
  options: { forceRefresh?: boolean } = {},
): Promise<LinkedInEnrichment> {
  const params = options.forceRefresh ? '?force_refresh=true' : '';
  return apiFetch<LinkedInEnrichment>(
    `/v1/leads/${leadId}/enrich/linkedin${params}`,
    { method: 'POST' },
  );
}
