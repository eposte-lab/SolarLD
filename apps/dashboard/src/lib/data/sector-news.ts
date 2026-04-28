/**
 * Sector news data layer — Sprint 10.
 *
 * Wraps the FastAPI endpoints at /v1/sector-news/* used by
 * the /settings/sector-news page.
 */
import { apiFetch } from '../api-client';

export interface SectorNews {
  id: string;
  tenant_id: string | null; // null = global seed (read-only for tenants)
  ateco_2digit: string;
  headline: string;
  body: string;
  source_url: string | null;
  status: 'active' | 'archived';
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ListSectorNewsResponse {
  rows: SectorNews[];
  total: number;
}

export interface CreateSectorNewsInput {
  ateco_2digit: string;
  headline: string;
  body: string;
  source_url?: string | null;
}

export interface UpdateSectorNewsInput {
  ateco_2digit?: string;
  headline?: string;
  body?: string;
  source_url?: string | null;
  status?: 'active' | 'archived';
}

export async function listSectorNews(): Promise<ListSectorNewsResponse> {
  return apiFetch<ListSectorNewsResponse>('/v1/sector-news/');
}

export async function createSectorNews(
  input: CreateSectorNewsInput,
): Promise<SectorNews> {
  return apiFetch<SectorNews>('/v1/sector-news/', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export async function updateSectorNews(
  newsId: string,
  input: UpdateSectorNewsInput,
): Promise<SectorNews> {
  return apiFetch<SectorNews>(`/v1/sector-news/${newsId}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  });
}

export async function archiveSectorNews(newsId: string): Promise<void> {
  await apiFetch(`/v1/sector-news/${newsId}`, { method: 'DELETE' });
}
