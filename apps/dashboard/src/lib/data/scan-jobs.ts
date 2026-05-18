/**
 * Scan jobs data layer — /territorio refactor totale.
 *
 * Una scan_job rappresenta una "lista di lavoro" definita dall'operatore:
 * territorio (regione/provincia/comune) + settori + cap giornaliero
 * di lead validati. Il worker la consuma per priority order e si ferma
 * al cap, ripartendo il giorno dopo.
 */

import { apiFetch } from '../api-client';

export type ScanJobStatus =
  | 'pending'
  | 'in_progress'
  | 'paused'
  | 'paused_daily_cap'
  | 'exhausted'
  | 'completed'
  | 'archived';

export interface ScanJob {
  id: string;
  name: string;
  region: string | null;
  province: string | null;
  comune: string | null;
  sector_filters: string[];
  daily_validated_cap: number;
  total_validated_cap: number;
  priority: number;
  status: ScanJobStatus;
  always_active: boolean;
  valid_leads_total: number;
  valid_leads_today: number;
  valid_leads_today_date: string | null;
  candidates_scanned_total: number;
  last_run_at: string | null;
  last_error: string | null;
  created_at: string;
  // Saturazione del territorio (aggregato lato API).
  zones_total: number;
  zones_depleted: number;
  candidates_in_queue: number;
}

export interface CreateScanJobInput {
  name: string;
  region?: string;
  province?: string;
  comune?: string;
  sector_filters?: string[];
  daily_validated_cap?: number;
  total_validated_cap?: number;
  always_active?: boolean;
}

export interface UpdateScanJobInput {
  name?: string;
  sector_filters?: string[];
  daily_validated_cap?: number;
  total_validated_cap?: number;
  always_active?: boolean;
  status?: ScanJobStatus;
}

export async function listScanJobs(): Promise<ScanJob[]> {
  return apiFetch<ScanJob[]>('/v1/territory/scan-jobs');
}

export async function createScanJob(input: CreateScanJobInput): Promise<ScanJob> {
  return apiFetch<ScanJob>('/v1/territory/scan-jobs', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export async function updateScanJob(
  id: string,
  input: UpdateScanJobInput,
): Promise<ScanJob> {
  return apiFetch<ScanJob>(`/v1/territory/scan-jobs/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  });
}

export async function reorderScanJobs(jobIds: string[]): Promise<{ reordered: number }> {
  return apiFetch('/v1/territory/scan-jobs/reorder', {
    method: 'POST',
    body: JSON.stringify({ job_ids: jobIds }),
  });
}

export async function deleteScanJob(id: string): Promise<{ archived: boolean; id: string }> {
  return apiFetch(`/v1/territory/scan-jobs/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
}
