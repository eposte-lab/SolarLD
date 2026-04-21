/**
 * `/onboarding` — modular wizard (v2 sole onboarding path).
 *
 * Replaces the v1 six-step wizard that was tied to `tenant_configs`.
 * The five-module flow writes to `tenant_modules`; completion flips
 * the onboarding-pending guard in the dashboard shell.
 *
 * Rendered by `ModularWizardShell`. The server component hydrates
 * existing module rows so the installer sees their prior answers
 * (or schema defaults on a clean tenant) rather than a blank form.
 */

import { redirect } from 'next/navigation';

import { ModularWizardShell } from '@/components/modules/ModularWizardShell';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { getModulesForTenant } from '@/lib/data/modules.server';

export default async function OnboardingPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const modules = await getModulesForTenant(ctx.tenant.id);

  return (
    <div className="space-y-8">
      <header>
        <h1 className="font-headline text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">
          Configura SolarLead per moduli.
        </h1>
        <p className="mt-3 max-w-2xl text-base text-on-surface-variant">
          Cinque moduli indipendenti — Sorgente, Tecnico, Economico,
          Outreach, CRM. Puoi saltare quelli che non ti servono ora e
          tornarci dopo dalle impostazioni.
        </p>
      </header>

      <ModularWizardShell modules={modules} />
    </div>
  );
}
