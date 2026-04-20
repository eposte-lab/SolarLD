/**
 * Tier gating — single source of truth for what each commercial
 * tier can do, plus helpers to answer "can tenant X use feature Y?".
 *
 * No Stripe yet (by product decision): tier activation is manual via
 * the admin API (`PATCH /system/tenants/:id/feature-flags` or
 * `PATCH /system/tenants/:id`). This file decides the *effect* of a
 * tier once set — the dashboard reads it, and `apps/api/src/core/tier.py`
 * mirrors the same matrix server-side.
 *
 * Resolution order (first wins):
 *   1. Explicit override in `tenants.settings.feature_flags[<key>]`
 *      — admin-gifted access, e.g. a founding-tier pilot that gets
 *      `realtime_timeline: true` to test the feature.
 *   2. Default from `CAPABILITIES[tier][<key>]`.
 *
 * Budgets are numeric and follow the same resolution rule but with
 * `null` meaning "unlimited" (enterprise tier).
 */

import type { TenantRow, TenantTier } from '@/types/db';

// ---------------------------------------------------------------------------
// Capability matrix — keep in sync with apps/api/src/core/tier.py
// ---------------------------------------------------------------------------

export type CapabilityKey =
  | 'email_outreach'
  | 'postal_outreach'
  | 'whatsapp_outreach'
  | 'realtime_timeline'
  | 'advanced_analytics'
  | 'template_editor'
  | 'ab_testing_templates'
  | 'crm_outbound_webhooks'
  | 'api_access'
  | 'custom_brand_domain'
  | 'bulk_export';

export type BudgetKey =
  | 'monthly_scan_budget_cents'
  | 'monthly_outreach_budget_cents';

type TierCapabilities = Record<CapabilityKey, boolean>;
type TierBudgets = Record<BudgetKey, number | null>; // null = unlimited

export interface TierSnapshot {
  tier: TenantTier;
  capabilities: TierCapabilities;
  budgets: TierBudgets;
  /** Whether any capability was lifted by an explicit feature_flag. */
  hasOverrides: boolean;
}

const CAPABILITIES: Record<TenantTier, TierCapabilities> = {
  founding: {
    email_outreach: true,
    postal_outreach: false,
    whatsapp_outreach: false,
    realtime_timeline: false,
    advanced_analytics: false,
    template_editor: false,
    ab_testing_templates: false,
    crm_outbound_webhooks: false,
    api_access: false,
    custom_brand_domain: false,
    bulk_export: false,
  },
  pro: {
    email_outreach: true,
    postal_outreach: true,
    whatsapp_outreach: true,
    realtime_timeline: true,
    advanced_analytics: true,
    template_editor: false,
    ab_testing_templates: false,
    crm_outbound_webhooks: true,
    api_access: false,
    custom_brand_domain: true,
    bulk_export: true,
  },
  enterprise: {
    email_outreach: true,
    postal_outreach: true,
    whatsapp_outreach: true,
    realtime_timeline: true,
    advanced_analytics: true,
    template_editor: true,
    ab_testing_templates: true,
    crm_outbound_webhooks: true,
    api_access: true,
    custom_brand_domain: true,
    bulk_export: true,
  },
};

const BUDGETS: Record<TenantTier, TierBudgets> = {
  founding:   { monthly_scan_budget_cents: 15_000, monthly_outreach_budget_cents: 10_000 },
  pro:        { monthly_scan_budget_cents: 50_000, monthly_outreach_budget_cents: 40_000 },
  enterprise: { monthly_scan_budget_cents: null,   monthly_outreach_budget_cents: null   },
};

/** Friendly label shown in upgrade prompts. */
export const TIER_LABEL: Record<TenantTier, string> = {
  founding: 'Founding',
  pro: 'Pro',
  enterprise: 'Enterprise',
};

/**
 * Ordered list used for "upgrade to next tier" copy. A feature locked
 * at founding unlocks at the lowest tier in this order that grants it.
 */
export const TIER_ORDER: readonly TenantTier[] = ['founding', 'pro', 'enterprise'];

// ---------------------------------------------------------------------------
// Resolvers
// ---------------------------------------------------------------------------

/**
 * Pure function — given a tenant row, return the full tier snapshot.
 * Overrides from `settings.feature_flags` are applied as booleans
 * (true/false both override the tier default).
 */
export function resolveTierSnapshot(tenant: TenantRow): TierSnapshot {
  const base = CAPABILITIES[tenant.tier];
  const flags = tenant.settings?.feature_flags ?? {};
  const capabilities = { ...base } as TierCapabilities;
  let hasOverrides = false;

  for (const key of Object.keys(capabilities) as CapabilityKey[]) {
    const flagValue = flags[key];
    if (typeof flagValue === 'boolean' && flagValue !== base[key]) {
      capabilities[key] = flagValue;
      hasOverrides = true;
    }
  }

  return {
    tier: tenant.tier,
    capabilities,
    budgets: BUDGETS[tenant.tier],
    hasOverrides,
  };
}

/** True iff the tenant is allowed to use the given feature. */
export function canTenantUse(
  tenant: TenantRow,
  feature: CapabilityKey,
): boolean {
  return resolveTierSnapshot(tenant).capabilities[feature];
}

/**
 * Monthly budget for a resource, applying tier default + overrides.
 * Returns null for "unlimited" (enterprise, or an explicit flag like
 * `monthly_scan_budget_cents: 0` wins as-is if set to null in settings).
 */
export function getTierBudget(
  tenant: TenantRow,
  key: BudgetKey,
): number | null {
  const override = (tenant.settings as Record<string, unknown> | null)?.[key];
  if (override === null) return null;
  if (typeof override === 'number' && Number.isFinite(override)) {
    return override;
  }
  return BUDGETS[tenant.tier][key];
}

/**
 * Lowest tier that grants the feature. Used by the lock overlay to
 * phrase the upgrade CTA correctly ("Upgrade a Pro per …").
 */
export function minimumTierFor(feature: CapabilityKey): TenantTier {
  for (const t of TIER_ORDER) {
    if (CAPABILITIES[t][feature]) return t;
  }
  // Defensive — every capability should unlock at enterprise.
  return 'enterprise';
}
