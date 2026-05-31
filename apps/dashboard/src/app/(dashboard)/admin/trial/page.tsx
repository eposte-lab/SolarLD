/**
 * Super-admin "trial moderation" queue.
 *
 * Two panels, both invisible to the moderated tenant:
 *   1. Coda lead — service-role view of every lead (RLS-bypassing) for a
 *      moderated tenant. "Far comparire" releases a lead (sets
 *      operator_released_at) so the tenant's RLS SELECT lets it through;
 *      "Tieni nascosto" holds it.
 *   2. Coda inbound — held prospect appointment requests
 *      (`pending_inbound_requests`). "Approva" replays the side-effects
 *      (tenant email + webhook + event + lead release); "Rifiuta" discards
 *      them with no tenant-facing trace.
 *
 * The whole route is gated `ctx.role === 'super_admin'` (a JWT claim, NOT a
 * tenant_members role) — exactly like DevToolsCard / pipeline-test. A
 * non-super-admin gets a 404 so the surface leaves no visible trace.
 */

import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';

import { TrialModerationPanel } from '@/components/admin/trial-moderation-panel';
import { getCurrentTenantContext } from '@/lib/data/tenant';

export const dynamic = 'force-dynamic';

// Default moderated tenant for the current trial (Total Trade). The panel
// lets the operator override it, but pre-filling the known trial tenant
// saves a copy-paste. Not a secret — just a tenant UUID.
const DEFAULT_MODERATED_TENANT = 'df08df04-4c90-4613-b21e-80879fc958d1';

export default async function TrialModerationPage({
  searchParams,
}: {
  searchParams: Promise<{ tenant_id?: string }>;
}) {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');
  // Hidden surface: anyone who isn't a super-admin must not learn it exists.
  if (ctx.role !== 'super_admin') notFound();

  const sp = await searchParams;
  const tenantId = sp.tenant_id?.trim() || DEFAULT_MODERATED_TENANT;

  return (
    <div className="space-y-8">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          <Link href="/settings" className="hover:text-on-surface hover:underline">
            Impostazioni
          </Link>
          {' · '}Trial moderation
        </p>
        <h1 className="mt-1 font-headline text-2xl font-bold tracking-tighter text-on-surface md:text-5xl">
          Coda di moderazione
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
          Cura cosa vede un tenant in trial moderata. I lead restano nascosti
          finché non li fai comparire; le richieste inbound dei prospect ti
          arrivano prima e raggiungono il tenant solo dopo la tua approvazione.
          Tutto questo è invisibile al tenant.
        </p>
      </header>

      <TrialModerationPanel initialTenantId={tenantId} />
    </div>
  );
}
