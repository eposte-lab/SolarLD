/**
 * Tenant operational config — server-side data access (Sprint 9).
 *
 * Mirrors the Python `tenant_config_service` DAO: every caller goes
 * through the typed `TenantConfigRow` + default fallback, never a
 * raw Supabase row.
 *
 * Primary consumers:
 *
 * 1. **Onboarding guard** — the dashboard root layout checks
 *    `wizard_completed_at`: if null, redirect to `/onboarding`.
 *
 * 2. **Settings page** — renders the current config read-only (Phase
 *    C will add editable forms that POST to the API).
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';
import type { AtecoOption, TenantConfigRow } from '@/types/db';

/**
 * Safe defaults used when a tenant has no row yet. This should only
 * happen for brand-new signups before the backfill has run — in
 * practice migration 0013 seeds every existing tenant.
 *
 * Keep in sync with `tenant_config_service._default_for` on the API.
 */
function defaultConfig(tenantId: string): TenantConfigRow {
  return {
    tenant_id: tenantId,
    scan_mode: 'opportunistic',
    target_segments: ['b2b', 'b2c'],
    place_type_whitelist: ['establishment'],
    place_type_priority: {},
    ateco_whitelist: [],
    ateco_blacklist: [],
    ateco_priority: {},
    min_employees: null,
    max_employees: null,
    min_revenue_eur: null,
    max_revenue_eur: null,
    technical_filters: {
      b2b: { min_area_sqm: 500, min_kwp: 50, max_shading: 0.4, min_exposure_score: 0.7 },
      b2c: { min_area_sqm: 60, min_kwp: 3, max_shading: 0.5, min_exposure_score: 0.6 },
    },
    scoring_threshold: 60,
    scoring_weights: {},
    monthly_scan_budget_eur: 1500,
    monthly_outreach_budget_eur: 2000,
    scan_priority_zones: ['capoluoghi'],
    scan_grid_density_m: 30,
    atoka_enabled: false,
    atoka_monthly_cap_eur: 0,
    wizard_completed_at: null,
  };
}

/**
 * Fetch the tenant's operational config. Returns the safe default
 * (opportunistic, `wizard_completed_at=null`) when the row is missing
 * so callers can always read `.scan_mode` / `.wizard_completed_at`
 * without null-checks on the root object.
 */
export async function getTenantConfig(tenantId: string): Promise<TenantConfigRow> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('tenant_configs')
    .select('*')
    .eq('tenant_id', tenantId)
    .maybeSingle();

  if (error) {
    // RLS or network error — log and fall back so the dashboard stays
    // functional. The onboarding redirect guard will still fire because
    // wizard_completed_at is null in the default.
    console.error('getTenantConfig.error', { tenantId, error: error.message });
    return defaultConfig(tenantId);
  }
  if (!data) return defaultConfig(tenantId);

  return data as TenantConfigRow;
}

/**
 * True when the tenant still needs to run the 5-step wizard.
 *
 * Used by the app-shell layout:
 *
 *   const cfg = await getTenantConfig(tenantId);
 *   if (isWizardPending(cfg)) redirect('/onboarding');
 */
export function isWizardPending(cfg: TenantConfigRow): boolean {
  return cfg.wizard_completed_at === null;
}

/**
 * Wizard dropdown options grouped by `wizard_group`. Server-side
 * read — the client never sees the raw `ateco_google_types` table.
 */
export async function listAtecoOptions(): Promise<AtecoOption[]> {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from('ateco_google_types')
    .select('ateco_code, ateco_label, wizard_group, google_types, priority_hint, target_segment')
    .order('wizard_group', { ascending: true })
    .order('priority_hint', { ascending: false });

  if (error) {
    console.error('listAtecoOptions.error', { error: error.message });
    return [];
  }
  return (data ?? []) as AtecoOption[];
}
