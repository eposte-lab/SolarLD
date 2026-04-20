import { redirect } from 'next/navigation';

import { getCurrentTenantContext } from '@/lib/data/tenant';
import { getTenantConfig, isWizardPending } from '@/lib/data/tenantConfig';

/**
 * Onboarding-only shell.
 *
 * Lives in a parallel route group so the `(dashboard)` layout's
 * wizard-pending guard doesn't wrap it — otherwise a fresh tenant
 * would loop between `/` and `/onboarding`.
 *
 * Behavior:
 *   - No auth session → `/login`
 *   - Already onboarded → `/` (bounce back to dashboard)
 *   - Otherwise → render the wizard on a full-bleed surface
 */

export default async function OnboardingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const cfg = await getTenantConfig(ctx.tenant.id);
  if (!isWizardPending(cfg)) {
    redirect('/');
  }

  return (
    <div className="min-h-screen bg-surface">
      <div className="mx-auto max-w-4xl px-6 py-12 md:py-20">{children}</div>
    </div>
  );
}
