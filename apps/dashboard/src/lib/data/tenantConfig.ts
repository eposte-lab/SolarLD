/**
 * Onboarding gate — backed by `tenant_modules`.
 *
 * After the April 2026 v2 cleanup there is no longer a `tenant_configs`
 * table. The five-module wizard (`tenant_modules`) is the single source
 * of truth for whether a tenant has finished onboarding.
 *
 * Heuristic: a module counts as "touched" once its row exists with
 * `version >= 1`. The API's modular-wizard endpoints bump version on
 * every save; the backfill migration (0035) seeds rows at version=1
 * for pre-existing tenants. So a brand-new signup has zero rows ⇒
 * onboarding pending; an installer who skipped every step still ends
 * up with five version=1 rows ⇒ onboarding done.
 */
import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';

const REQUIRED_MODULES = [
  'sorgente',
  'tecnico',
  'economico',
  'outreach',
  'crm',
] as const;

/**
 * True while the tenant hasn't completed the modular onboarding yet.
 * Callers redirect to `/onboarding` when this returns true.
 */
export async function isOnboardingPending(tenantId: string): Promise<boolean> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('tenant_modules')
    .select('module_key')
    .eq('tenant_id', tenantId);

  if (error) {
    // Fail open: if we can't read we let the user through. The API
    // endpoints re-check on every write, so a corrupt read here only
    // skips the redirect, it doesn't bypass any real gate.
    console.error('isOnboardingPending.error', {
      tenantId,
      error: error.message,
    });
    return false;
  }

  const present = new Set((data ?? []).map((r) => r.module_key));
  return REQUIRED_MODULES.some((k) => !present.has(k));
}

/**
 * Last step of onboarding: the installer must confirm (and freeze) the
 * territorial exclusivity. Derived purely from the already-loaded
 * tenant row — no DB call — so layouts can chain it cheaply after
 * `isOnboardingPending`.
 */
export function isTerritoryConfirmPending(
  tenant: { territory_locked_at?: string | null },
): boolean {
  return !tenant.territory_locked_at;
}
