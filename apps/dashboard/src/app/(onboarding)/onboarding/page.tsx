/**
 * `/onboarding` — 5-step configuration wizard for a new tenant.
 *
 * Auth + wizard-pending guards run in the parent `(onboarding)` layout.
 * This server component just fetches the ATECO option list (grouped by
 * vertical, served from `ateco_google_types`) and hands it to the client
 * `WizardShell` so Step 2 can render the accordion without a round-trip.
 */

import { WizardShell } from '@/components/onboarding/WizardShell';
import { listAtecoOptions } from '@/lib/data/tenantConfig';

export default async function OnboardingPage() {
  const options = await listAtecoOptions();

  return (
    <div className="space-y-8">
      <header>
        <h1 className="font-headline text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">
          Benvenuto in SolarLead.
        </h1>
        <p className="mt-3 max-w-2xl text-base text-on-surface-variant">
          Cinque passi per dire a Hunter cosa cercare, dove cercarlo e
          quanto spendere. Potrai modificare ogni scelta più tardi dalle
          impostazioni.
        </p>
      </header>

      <WizardShell options={options} />
    </div>
  );
}
