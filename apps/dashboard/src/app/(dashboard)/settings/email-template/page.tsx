/**
 * /settings/email-template — Sprint 9 Fase C.5
 *
 * Three-tab page:
 *   Tab 1 "Template"  — Choose between Premium SolarLead / Legacy / Custom HTML
 *   Tab 2 "A/B Test"  — Cluster-level A/B engine: active pairs, manual overrides
 *
 * Server component: fetches initial data, passes to client panels.
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { EmailTemplatePageClient } from './page-client';
import { getCurrentTenantContext } from '@/lib/data/tenant';

export const dynamic = 'force-dynamic';

export default async function EmailTemplatePage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  return (
    <div className="space-y-8">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          <Link href="/settings" className="hover:text-on-surface hover:underline">
            Impostazioni
          </Link>
          {' · '}Template email
        </p>
        <h1 className="mt-1 font-headline text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">
          Template & A/B test email
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
          Scegli il template di default, carica un HTML personalizzato o gestisci
          i test A/B per-cluster che ottimizzano il copy automaticamente.
        </p>
      </header>

      <EmailTemplatePageClient />
    </div>
  );
}
