'use client';

/**
 * Client-side access to the `/v1/modules/*` endpoints.
 *
 * Mirror of `apps/api/src/routes/modules.py`. These helpers return
 * parsed TenantModule objects (or throw ApiError) and are safe to call
 * from React components / hooks.
 *
 * We intentionally don't cache at this layer — the modular wizard and
 * settings pages both want fresh reads after every mutation. React
 * Query / SWR can wrap these later if we need smarter invalidation.
 */

import { api } from '@/lib/api-client';

import type {
  ModuleConfigByKey,
  ModuleKey,
  ModuleListResponse,
  ModulePreviewResponse,
  TenantModule,
} from '@/types/modules';

export async function listModules(): Promise<ModuleListResponse> {
  return api.get<ModuleListResponse>('/v1/modules');
}

export async function getModule<K extends ModuleKey>(
  key: K,
): Promise<TenantModule<K>> {
  return api.get<TenantModule<K>>(`/v1/modules/${key}`);
}

/**
 * Upsert one module. Pass `config` to update the body; pass `active`
 * to toggle enablement without touching config. Backend errors out
 * with 422 on shape violations — the caller should surface the
 * validation error list from `ApiError.body.detail`.
 */
export async function upsertModule<K extends ModuleKey>(
  key: K,
  payload: {
    config?: ModuleConfigByKey[K];
    active?: boolean;
  },
): Promise<TenantModule<K>> {
  return api.put<TenantModule<K>>(`/v1/modules/${key}`, payload);
}

export async function previewModule<K extends ModuleKey>(
  key: K,
  config: ModuleConfigByKey[K],
): Promise<ModulePreviewResponse<ModuleConfigByKey[K]>> {
  return api.post<ModulePreviewResponse<ModuleConfigByKey[K]>>(
    `/v1/modules/${key}/preview`,
    { config },
  );
}
