import { redirect } from 'next/navigation';

import { getCurrentTenantContext } from '@/lib/data/tenant';
import {
  isOnboardingPending,
  isTerritoryConfirmPending,
} from '@/lib/data/tenantConfig';

/**
 * Onboarding-only shell.
 *
 * Lives in a parallel route group so the `(dashboard)` layout's
 * wizard-pending guard doesn't wrap it — otherwise a fresh tenant
 * would loop between `/` and `/onboarding`.
 *
 * Behavior:
 *   - No auth session → `/login`
 *   - Already onboarded (all 5 modules present) → `/`
 *   - Otherwise → render the modular wizard on a full-bleed surface
 */

export default async function OnboardingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const pending = await isOnboardingPending(ctx.tenant.id);
  const territoryPending = isTerritoryConfirmPending(ctx.tenant);

  // Already fully onboarded (5 modules + territory confirmed) → dashboard.
  if (!pending && !territoryPending) {
    redirect('/');
  }

  return (
    <div className="min-h-screen bg-surface">
      <div className="mx-auto max-w-4xl px-6 py-12 md:py-20">{children}</div>
    </div>
  );
}
