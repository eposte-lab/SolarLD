/**
 * Analytics data access — calls the `analytics_*` Postgres RPCs
 * defined in migration 0016.
 *
 * All functions are `SECURITY DEFINER` and take an explicit
 * `p_tenant_id`; we resolve the current tenant from the session on
 * each call. RLS on the underlying tables ensures the user can only
 * pass a tenant_id they actually belong to (we verify via
 * `tenant_members`).
 */

import 'server-only';

import { createSupabaseServerClient } from '@/lib/supabase/server';
import { getCurrentTenantContext } from '@/lib/data/tenant';

// ---------------------------------------------------------------------------
// Shared payload types — mirror the JSONB returned by the Postgres funcs
// ---------------------------------------------------------------------------

export interface FunnelCounts {
  leads_total: number;
  sent: number;
  delivered: number;
  opened: number;
  clicked: number;
  engaged: number;
  contract_signed: number;
  hot: number;
  warm: number;
  cold: number;
  rejected: number;
}

export interface SpendByProviderRow {
  provider: string;
  calls: number;
  cost_cents: number;
  errors: number;
}

export interface SpendDailyPoint {
  day: string; // YYYY-MM-DD
  cost_cents: number;
  calls: number;
}

export interface TerritoryRoiRow {
  territory_id: string;
  territory_name: string;
  leads_total: number;
  leads_hot: number;
  avg_score: number;
  signed: number;
  contract_value_eur: number;
}

export interface UsageMtd {
  roofs_scanned_mtd: number;
  leads_generated_mtd: number;
  emails_sent_mtd: number;
  postcards_sent_mtd: number;
  total_cost_eur: number;
}

// ---------------------------------------------------------------------------
// Tenant-scoped helpers
// ---------------------------------------------------------------------------

async function tenantIdOrThrow(): Promise<string> {
  const ctx = await getCurrentTenantContext();
  if (!ctx) throw new Error('analytics: no tenant in session');
  return ctx.tenant.id;
}

/** MTD operational counters — roofs, leads, emails, postcards, cost. */
export async function getUsageMtd(): Promise<UsageMtd> {
  const sb = await createSupabaseServerClient();
  const tenant_id = await tenantIdOrThrow();
  const { data, error } = await sb.rpc('analytics_usage_mtd', {
    p_tenant_id: tenant_id,
  });
  if (error) throw new Error(`analytics_usage_mtd: ${error.message}`);
  return (data as UsageMtd) ?? {
    roofs_scanned_mtd: 0,
    leads_generated_mtd: 0,
    emails_sent_mtd: 0,
    postcards_sent_mtd: 0,
    total_cost_eur: 0,
  };
}

/** Funnel counts over a trailing window (default 30 days). */
export async function getFunnel(days = 30): Promise<FunnelCounts> {
  const sb = await createSupabaseServerClient();
  const tenant_id = await tenantIdOrThrow();
  const to = new Date();
  const from = new Date(to.getTime() - days * 24 * 60 * 60 * 1000);
  const { data, error } = await sb.rpc('analytics_funnel', {
    p_tenant_id: tenant_id,
    p_from: from.toISOString(),
    p_to: to.toISOString(),
  });
  if (error) throw new Error(`analytics_funnel: ${error.message}`);
  return (data as FunnelCounts) ?? {
    leads_total: 0,
    sent: 0,
    delivered: 0,
    opened: 0,
    clicked: 0,
    engaged: 0,
    contract_signed: 0,
    hot: 0,
    warm: 0,
    cold: 0,
    rejected: 0,
  };
}

/** MTD provider cost rollup. */
export async function getSpendByProvider(): Promise<SpendByProviderRow[]> {
  const sb = await createSupabaseServerClient();
  const tenant_id = await tenantIdOrThrow();
  const { data, error } = await sb.rpc('analytics_spend_by_provider', {
    p_tenant_id: tenant_id,
  });
  if (error) throw new Error(`analytics_spend_by_provider: ${error.message}`);
  return (data as SpendByProviderRow[]) ?? [];
}

/** Daily spend sparkline — gap-filled for every day in window. */
export async function getSpendDaily(days = 30): Promise<SpendDailyPoint[]> {
  const sb = await createSupabaseServerClient();
  const tenant_id = await tenantIdOrThrow();
  const { data, error } = await sb.rpc('analytics_spend_daily', {
    p_tenant_id: tenant_id,
    p_days: days,
  });
  if (error) throw new Error(`analytics_spend_daily: ${error.message}`);
  return (data as SpendDailyPoint[]) ?? [];
}

/** Territory ROI — per-territory counts + signed contract value. */
export async function getTerritoryRoi(): Promise<TerritoryRoiRow[]> {
  const sb = await createSupabaseServerClient();
  const tenant_id = await tenantIdOrThrow();
  const { data, error } = await sb.rpc('analytics_territory_roi', {
    p_tenant_id: tenant_id,
  });
  if (error) throw new Error(`analytics_territory_roi: ${error.message}`);
  return (data as TerritoryRoiRow[]) ?? [];
}
