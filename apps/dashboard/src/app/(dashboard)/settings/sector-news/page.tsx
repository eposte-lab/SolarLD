/**
 * /settings/sector-news — Sprint 10.
 *
 * Operator-curated catalogue of sector-relevant signals consumed by the
 * engagement-based follow-up engine (``followup_engaged.j2`` /
 * ``followup_lukewarm.j2``). The point: these copies must NEVER quote
 * tracked behaviour ("you opened our email"), so they need a non-creepy
 * hook — a sector fact, ATECO 2-digit-bucketed.
 *
 * Server component is a thin shell — the table + create/edit modal live
 * in the client component because they need optimistic state.
 */
import Link from 'next/link';
import { redirect } from 'next/navigation';

import { getCurrentTenantContext } from '@/lib/data/tenant';
import { SectorNewsPageClient } from './page-client';

export const dynamic = 'force-dynamic';

export default async function SectorNewsPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  return (
    <div className="space-y-8">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          <Link href="/settings" className="hover:text-on-surface hover:underline">
            Impostazioni
          </Link>
          {' · '}News di settore
        </p>
        <h1 className="mt-1 font-headline text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">
          News di settore
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
          Curate qui i fatti di settore (incentivi, normative, prezzi energia)
          che il motore di follow-up cita nei messaggi quando un lead mostra
          interesse. Nessuna mail farà mai riferimento al fatto che il lead
          abbia aperto la precedente — l&apos;aggancio è sempre un dato di
          settore. Le righe globali (precaricate da SolarLead) sono read-only;
          puoi sovrascriverle creando una versione tua per lo stesso codice
          ATECO 2-digit.
        </p>
      </header>

      <SectorNewsPageClient tenantId={ctx.tenant.id} />
    </div>
  );
}
