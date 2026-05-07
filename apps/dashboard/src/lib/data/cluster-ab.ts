/**
 * Cluster A/B test data layer — Sprint 9 Fase B.6.
 *
 * Wraps the FastAPI endpoints at /v1/cluster-ab/* for use by
 * the /settings/email-template page.
 */
import { apiFetch, API_URL } from '../api-client';
import { createBrowserClient } from '../supabase/client';

export interface VariantCopy {
  id: string;
  variant_label: 'A' | 'B';
  round_number: number;
  status: 'active' | 'winner' | 'loser' | 'no_difference' | 'archived';
  copy_subject: string;
  copy_opening_line: string;
  copy_proposition_line: string;
  cta_primary_label: string;
  generated_by: string;
  sent_count: number;
  replied_count: number;
  reply_rate: number | null;
}

export interface ClusterAB {
  cluster_signature: string;
  round_number: number;
  variants: VariantCopy[];
  prob_a_wins: number | null;
  /**
   * When set, the cluster has converged: the OutreachAgent serves
   * 100% of the traffic to ``champion_variant_id`` and no new rounds
   * are generated. The drift cron (90 days) or the operator
   * "Sfida il vincitore" button restarts testing.
   */
  converged_at?: string | null;
  champion_variant_id?: string | null;
}

export interface ActiveClustersResponse {
  clusters: ClusterAB[];
  total: number;
}

export interface DailyMetric {
  round_number: number;
  variant_label: 'A' | 'B';
  date: string;
  sent_count: number;
  replied_count: number;
  reply_rate: number | null;
}

export interface ClusterDetailResponse {
  cluster_signature: string;
  variants: VariantCopy[];
  active_round: number | null;
  prob_a_wins: number | null;
  daily_metrics: DailyMetric[];
}

export interface PromoteResult {
  promoted: string;
  cluster_signature: string;
  new_round: number;
}

export interface RegenerateResult {
  cluster_signature: string;
  archived_count: number;
  new_round: number;
}

// ── API calls ──────────────────────────────────────────────────────

export async function listActiveClusters(): Promise<ActiveClustersResponse> {
  return apiFetch<ActiveClustersResponse>('/v1/cluster-ab/active');
}

export async function getClusterDetail(
  clusterSignature: string,
): Promise<ClusterDetailResponse> {
  return apiFetch<ClusterDetailResponse>(`/v1/cluster-ab/${encodeURIComponent(clusterSignature)}`);
}

export async function promoteVariant(variantId: string): Promise<PromoteResult> {
  return apiFetch<PromoteResult>(`/v1/cluster-ab/${variantId}/promote`, { method: 'POST' });
}

export async function regenerateCluster(
  clusterSignature: string,
): Promise<RegenerateResult> {
  return apiFetch<RegenerateResult>(
    `/v1/cluster-ab/${encodeURIComponent(clusterSignature)}/regenerate`,
    { method: 'POST' },
  );
}

export interface UnlockResult {
  cluster_signature: string;
  new_round: number;
  unlocked_at: string;
}

/**
 * Manually challenge a converged cluster's champion. Resets
 * cluster_state and triggers a fresh A+B generation. The dashboard
 * "Sfida il vincitore" button calls this.
 */
export async function unlockConvergedCluster(
  clusterSignature: string,
): Promise<UnlockResult> {
  return apiFetch<UnlockResult>(
    `/v1/cluster-ab/${encodeURIComponent(clusterSignature)}/unlock`,
    { method: 'POST' },
  );
}

// ── Custom email template calls ────────────────────────────────────

export interface TemplateInfo {
  active: boolean;
  path: string | null;
  uploaded_at: string | null;
  required_variables: string[];
  optional_variables: string[];
}

export async function getEmailTemplateInfo(): Promise<TemplateInfo> {
  return apiFetch<TemplateInfo>('/v1/branding/email-template/info');
}

export async function uploadEmailTemplate(html: string): Promise<{ status: string; path: string }> {
  return apiFetch('/v1/branding/email-template', {
    method: 'POST',
    body: JSON.stringify({ html }),
  });
}

export async function deactivateEmailTemplate(): Promise<{ status: string }> {
  return apiFetch('/v1/branding/email-template', { method: 'DELETE' });
}

export async function getCustomTemplatePreviewUrl(): Promise<string> {
  const supabase = createBrowserClient();
  const { data: { session } } = await supabase.auth.getSession();
  const token = session?.access_token ?? '';
  // Return a URL that can be loaded in an iframe — the route returns HTML directly.
  return `${API_URL}/v1/branding/email-template/preview?token=${token}`;
}
