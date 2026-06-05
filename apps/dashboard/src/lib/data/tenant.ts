/**
 * Tenant context for the current authenticated user.
 *
 * RLS on `tenant_members` ensures each logged-in user only sees their
 * own membership row; the dashboard reads the first such row to
 * discover which tenant to scope pages to.
 */

import 'server-only';

import { cache } from 'react';

import { createSupabaseServerClient } from '@/lib/supabase/server';
import type { TenantRow } from '@/types/db';

export interface TenantContext {
  tenant: TenantRow;
  role: string;
  user_id: string;
  user_email: string | null;
  /**
   * True when this tenant is under super-admin trial moderation
   * (tenants.settings.feature_flags.trial_moderation). The tenant still
   * sees everything it normally would — contatti, invii, the schede of
   * the IDs it was sent. What this flag gates is the STATE promotion
   * contatto → lead: a row only surfaces as an active *lead* (the /leads
   * list, the hot-leads widgets and the hot-leads KPI) once the operator
   * has released it (`operator_released_at IS NOT NULL`). Engaged-but-
   * un-released rows stay contatti until the operator promotes them.
   * The flag is invisible to the tenant: it only ever changes which rows
   * count as leads, never surfaced as a label or disabled state.
   */
  is_moderated: boolean;
}

/**
 * Resolve the current tenant + member role for the logged-in user.
 *
 * Wrapped in React.cache so multiple callers within the same request
 * (layout + page) share a single DB round-trip instead of each running
 * their own auth.getUser + tenant_members JOIN query.
 *
 * Returns null when the session is missing or the user has no
 * `tenant_members` row yet (new signup, pending onboarding).
 */
export const getCurrentTenantContext = cache(async (): Promise<TenantContext | null> => {
  const supabase = await createSupabaseServerClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return null;

  // NOTE: territory_locked_at / territory_locked_by are fetched separately
  // in getTenantLockStatus() below so this query doesn't fail before
  // migration 0047 is applied (those columns didn't exist before it).
  const { data: member } = await supabase
    .from('tenant_members')
    .select('tenant_id, role, tenants:tenants(id, business_name, brand_primary_color, brand_logo_url, contact_email, whatsapp_number, email_from_domain, email_from_name, email_from_domain_verified_at, followup_from_email, tier, settings, demo_device_limit_enabled, demo_device_max_total, demo_device_idle_timeout_minutes, is_demo, demo_pipeline_test_remaining, outreach_blocked)')
    .eq('user_id', user.id)
    .limit(1)
    .maybeSingle();

  if (!member || !member.tenants) return null;

  // Supabase returns `tenants` as an object (singular FK). Older
  // versions of the client type it as an array; normalize defensively.
  const tenant = Array.isArray(member.tenants) ? member.tenants[0] : member.tenants;
  if (!tenant) return null;

  const tenantRow = tenant as TenantRow;
  // The flag is stored in JSONB and may be either the boolean `true` or
  // the JSON string "true" (migration 0146 writes the string form, the
  // PATCH feature-flags endpoint may write either). Mirror the SQL helper
  // tenant_is_moderated(), which reads it with `->>` (text) and compares
  // to 'true' — so both shapes resolve to moderated.
  const flag = tenantRow.settings?.feature_flags?.trial_moderation;
  const isModerated = flag === true || flag === 'true';

  return {
    tenant: tenantRow,
    role: member.role,
    user_id: user.id,
    user_email: user.email ?? null,
    is_moderated: isModerated,
  };
});

/**
 * True when the current tenant is under super-admin trial moderation.
 * Shared by every aggregate/analytics data function that must freeze
 * engagement of un-promoted contatti (operator_released_at IS NULL).
 */
export async function isModeratedTenant(): Promise<boolean> {
  const ctx = await getCurrentTenantContext();
  return ctx?.is_moderated ?? false;
}
