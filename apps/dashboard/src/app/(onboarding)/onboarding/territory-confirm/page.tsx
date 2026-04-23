/**
 * `/onboarding/territory-confirm` — last step of onboarding.
 *
 * Shows a review card of the installer's territorial footprint
 * (regioni / province / CAP from the `sorgente` module + the
 * `territories` rows they've added) and asks them to confirm the
 * exclusivity. Clicking "Confermo e blocco" calls
 * POST /v1/onboarding/territory-confirm which sets
 * `tenants.territory_locked_at = now()`; the dashboard then unblocks.
 *
 * The onboarding layout routes here automatically when:
 *   - all 5 modules are saved (`!isOnboardingPending`)
 *   - but `tenant.territory_locked_at` is still null.
 *
 * A tenant landing here with an already-locked timestamp is redirected
 * away by the layout guard, so this page can assume lock is pending.
 */

import { redirect } from 'next/navigation';

import { getCurrentTenantContext } from '@/lib/data/tenant';
import { getModuleForTenant } from '@/lib/data/modules.server';
import { listTerritories } from '@/lib/data/territories';
import type { SorgenteConfig } from '@/types/modules';

import { TerritoryConfirmCard } from './territory-confirm-card';

export default async function TerritoryConfirmPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  // If somehow already locked, hand off to the dashboard — the layout
  // gate should have caught this, but belt-and-braces.
  if (ctx.tenant.territory_locked_at) {
    redirect('/');
  }

  const [sorgente, territories] = await Promise.all([
    getModuleForTenant(ctx.tenant.id, 'sorgente'),
    listTerritories(),
  ]);

  const cfg = sorgente.config as SorgenteConfig;

  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Ultimo step · Esclusiva territoriale
        </p>
        <h1 className="font-headline text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">
          Conferma la tua zona di esclusiva.
        </h1>
        <p className="mt-2 max-w-2xl text-base text-on-surface-variant">
          L&apos;esclusiva territoriale è la garanzia contrattuale del
          tuo piano: nessun altro installatore opererà con SolarLead su
          questa area. Una volta confermata, solo il nostro supporto
          potrà modificarla.
        </p>
      </header>

      <TerritoryConfirmCard
        regioni={cfg.regioni ?? []}
        province={cfg.province ?? []}
        cap={cfg.cap ?? []}
        territories={territories.map((t) => ({
          id: t.id,
          type: t.type,
          code: t.code,
          name: t.name,
        }))}
      />
    </div>
  );
}
