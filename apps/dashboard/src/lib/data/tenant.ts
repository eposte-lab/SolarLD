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

  const { data: member } = await supabase
    .from('tenant_members')
    .select('tenant_id, role, tenants:tenants(id, business_name, brand_primary_color, brand_logo_url, contact_email, whatsapp_number, email_from_domain, email_from_name, email_from_domain_verified_at, tier, settings)')
    .eq('user_id', user.id)
    .limit(1)
    .maybeSingle();

  if (!member || !member.tenants) return null;

  // Supabase returns `tenants` as an object (singular FK). Older
  // versions of the client type it as an array; normalize defensively.
  const tenant = Array.isArray(member.tenants) ? member.tenants[0] : member.tenants;
  if (!tenant) return null;

  return {
    tenant: tenant as TenantRow,
    role: member.role,
    user_id: user.id,
    user_email: user.email ?? null,
  };
});
