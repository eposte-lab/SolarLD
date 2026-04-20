/**
 * Pure-function tests for the tier resolver.
 *
 * The capability matrix in `tier.ts` is the single source of truth for
 * what the dashboard shows, hides, or blurs behind an upgrade lock —
 * silent drift here would let pro-only features leak to founding tenants.
 */
import { describe, it, expect } from 'vitest';

import {
  canTenantUse,
  getTierBudget,
  minimumTierFor,
  resolveTierSnapshot,
} from './tier';
import type { TenantRow } from '@/types/db';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeTenant(
  tier: TenantRow['tier'],
  settings: Record<string, unknown> | null = null,
): TenantRow {
  return {
    id: 't-1',
    slug: 'test',
    name: 'Test Tenant',
    tier,
    status: 'active',
    settings,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  } as unknown as TenantRow;
}

// ---------------------------------------------------------------------------
// Default matrix
// ---------------------------------------------------------------------------

describe('resolveTierSnapshot — defaults', () => {
  it('founding has only email_outreach', () => {
    const s = resolveTierSnapshot(makeTenant('founding'));
    expect(s.capabilities.email_outreach).toBe(true);
    expect(s.capabilities.postal_outreach).toBe(false);
    expect(s.capabilities.whatsapp_outreach).toBe(false);
    expect(s.capabilities.realtime_timeline).toBe(false);
    expect(s.capabilities.api_access).toBe(false);
    expect(s.hasOverrides).toBe(false);
  });

  it('pro unlocks realtime, whatsapp, crm webhooks but not api_access', () => {
    const s = resolveTierSnapshot(makeTenant('pro'));
    expect(s.capabilities.realtime_timeline).toBe(true);
    expect(s.capabilities.whatsapp_outreach).toBe(true);
    expect(s.capabilities.crm_outbound_webhooks).toBe(true);
    expect(s.capabilities.api_access).toBe(false);
    expect(s.capabilities.ab_testing_templates).toBe(false);
  });

  it('enterprise unlocks everything', () => {
    const s = resolveTierSnapshot(makeTenant('enterprise'));
    for (const v of Object.values(s.capabilities)) {
      expect(v).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// Budgets
// ---------------------------------------------------------------------------

describe('getTierBudget', () => {
  it('returns founding default scan budget', () => {
    expect(
      getTierBudget(makeTenant('founding'), 'monthly_scan_budget_cents'),
    ).toBe(15_000);
  });

  it('returns pro default scan budget', () => {
    expect(
      getTierBudget(makeTenant('pro'), 'monthly_scan_budget_cents'),
    ).toBe(50_000);
  });

  it('enterprise is null (unlimited)', () => {
    expect(
      getTierBudget(makeTenant('enterprise'), 'monthly_scan_budget_cents'),
    ).toBeNull();
  });

  it('explicit numeric override wins over tier default', () => {
    const tenant = makeTenant('founding', {
      monthly_scan_budget_cents: 25_000,
    });
    expect(
      getTierBudget(tenant, 'monthly_scan_budget_cents'),
    ).toBe(25_000);
  });

  it('explicit null override means unlimited even on founding', () => {
    const tenant = makeTenant('founding', {
      monthly_scan_budget_cents: null,
    });
    expect(
      getTierBudget(tenant, 'monthly_scan_budget_cents'),
    ).toBeNull();
  });

  it('non-numeric override (string) falls back to tier default', () => {
    const tenant = makeTenant('pro', {
      monthly_scan_budget_cents: 'unlimited', // invalid shape
    });
    expect(
      getTierBudget(tenant, 'monthly_scan_budget_cents'),
    ).toBe(50_000);
  });
});

// ---------------------------------------------------------------------------
// Feature flag overrides
// ---------------------------------------------------------------------------

describe('feature_flags overrides', () => {
  it('unlocks realtime_timeline on a founding pilot tenant', () => {
    const tenant = makeTenant('founding', {
      feature_flags: { realtime_timeline: true },
    });
    const s = resolveTierSnapshot(tenant);
    expect(s.capabilities.realtime_timeline).toBe(true);
    expect(s.hasOverrides).toBe(true);
    expect(canTenantUse(tenant, 'realtime_timeline')).toBe(true);
  });

  it('can strip a capability from pro via a false flag', () => {
    // Rare but valid: a pro tenant found abusing WA sending has the
    // feature explicitly switched off.
    const tenant = makeTenant('pro', {
      feature_flags: { whatsapp_outreach: false },
    });
    expect(canTenantUse(tenant, 'whatsapp_outreach')).toBe(false);
    expect(resolveTierSnapshot(tenant).hasOverrides).toBe(true);
  });

  it('flag equal to the tier default does NOT count as an override', () => {
    const tenant = makeTenant('pro', {
      feature_flags: { realtime_timeline: true }, // already default
    });
    expect(resolveTierSnapshot(tenant).hasOverrides).toBe(false);
  });

  it('null settings are safe', () => {
    const tenant = makeTenant('founding', null);
    expect(canTenantUse(tenant, 'email_outreach')).toBe(true);
    expect(canTenantUse(tenant, 'postal_outreach')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// minimumTierFor
// ---------------------------------------------------------------------------

describe('minimumTierFor', () => {
  it('email_outreach is founding', () => {
    expect(minimumTierFor('email_outreach')).toBe('founding');
  });

  it('realtime_timeline is pro', () => {
    expect(minimumTierFor('realtime_timeline')).toBe('pro');
  });

  it('api_access is enterprise', () => {
    expect(minimumTierFor('api_access')).toBe('enterprise');
  });

  it('ab_testing_templates is enterprise', () => {
    expect(minimumTierFor('ab_testing_templates')).toBe('enterprise');
  });
});
