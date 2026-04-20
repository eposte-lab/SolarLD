/**
 * Settings → CRM outbound webhooks.
 *
 * Pro-tier+ feature (Part B.7). Founding tenants see the TierLock
 * upgrade overlay instead of the manager. The secret rotation flow
 * happens entirely in ``<CrmWebhooksManager>`` because it needs
 * client-side state (the one-shot reveal banner + clipboard copy).
 *
 * Initial rows are fetched via ``listCrmWebhooks`` (RLS-scoped SELECT,
 * secret column never selected) so first paint is instant. After
 * mutations the manager calls ``router.refresh()`` which re-enters
 * this page on the server and re-fetches.
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { CrmWebhooksManager } from '@/components/crm-webhooks-manager';
import { BentoCard } from '@/components/ui/bento-card';
import { TierLock } from '@/components/ui/tier-lock';
import { listCrmWebhooks } from '@/lib/data/crm-webhooks';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { canTenantUse } from '@/lib/data/tier';

export const dynamic = 'force-dynamic';

export default async function CrmWebhooksPage() {
  // Fire both in parallel. RLS on crm_webhooks enforces tenant isolation
  // so speculative fetch is safe even before the tier check resolves.
  const [ctx, speculativeRows] = await Promise.all([
    getCurrentTenantContext(),
    listCrmWebhooks().catch(() => []),
  ]);
  if (!ctx) redirect('/login');

  const rows = canTenantUse(ctx.tenant, 'crm_outbound_webhooks') ? speculativeRows : [];

  return (
    <div className="space-y-8">
      <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            <Link
              href="/settings"
              className="hover:text-on-surface hover:underline"
            >
              Impostazioni
            </Link>
            {' · '}Webhook CRM
          </p>
          <h1 className="mt-1 font-headline text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">
            Webhook CRM in uscita
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
            Ricevi sul tuo CRM gli eventi chiave del ciclo di vita del lead —
            creazione, scoring, invii, interazioni, contratto firmato. Il
            dispatcher firma ogni richiesta con HMAC-SHA256 e riprova con
            backoff esponenziale; 10 fallimenti consecutivi disattivano
            l&apos;endpoint per evitare di martellare un sistema offline.
          </p>
        </div>
      </header>

      <TierLock
        feature="crm_outbound_webhooks"
        tenant={ctx.tenant}
        featureLabel="Webhook CRM in uscita"
      >
        <BentoCard span="full">
          <CrmWebhooksManager initialRows={rows} />
        </BentoCard>
      </TierLock>
    </div>
  );
}
