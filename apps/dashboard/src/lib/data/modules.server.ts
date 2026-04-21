/**
 * Server-side fetcher for modules — reads directly from Supabase.
 *
 * The client helpers in `modules.ts` go through the FastAPI, which
 * needs a browser JWT. Server components render without a JWT so
 * they talk to Supabase directly (mirrors `tenantConfig.ts`).
 *
 * If a tenant has no rows yet, we synthesise the default row
 * structure with empty config `{}` — the client form components
 * render schema defaults when `config` is empty.
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';
import type { ModuleKey, TenantModule } from '@/types/modules';

const MODULE_KEYS: readonly ModuleKey[] = [
  'sorgente',
  'tecnico',
  'economico',
  'outreach',
  'crm',
] as const;

export async function getModulesForTenant(
  tenantId: string,
): Promise<TenantModule[]> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('tenant_modules')
    .select('tenant_id, module_key, config, active, version, updated_at')
    .eq('tenant_id', tenantId);

  if (error) {
    console.error('getModulesForTenant.error', {
      tenantId,
      error: error.message,
    });
    return synthesiseDefaults(tenantId);
  }

  const rows = (data ?? []) as TenantModule[];
  const byKey = new Map(rows.map((r) => [r.module_key, r]));
  const out: TenantModule[] = [];
  for (const key of MODULE_KEYS) {
    const row = byKey.get(key);
    if (row) {
      out.push(row);
    } else {
      out.push({
        tenant_id: tenantId,
        module_key: key,
        // biome-ignore lint/suspicious/noExplicitAny: default empty
        config: {} as any,
        active: true,
        version: 0,
      });
    }
  }
  return out;
}

export async function getModuleForTenant(
  tenantId: string,
  key: ModuleKey,
): Promise<TenantModule> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('tenant_modules')
    .select('tenant_id, module_key, config, active, version, updated_at')
    .eq('tenant_id', tenantId)
    .eq('module_key', key)
    .maybeSingle();

  if (error || !data) {
    return {
      tenant_id: tenantId,
      module_key: key,
      // biome-ignore lint/suspicious/noExplicitAny: default empty
      config: {} as any,
      active: true,
      version: 0,
    };
  }
  return data as TenantModule;
}

function synthesiseDefaults(tenantId: string): TenantModule[] {
  return MODULE_KEYS.map((k) => ({
    tenant_id: tenantId,
    module_key: k,
    // biome-ignore lint/suspicious/noExplicitAny: default empty
    config: {} as any,
    active: true,
    version: 0,
  }));
}
