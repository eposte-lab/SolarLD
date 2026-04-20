/**
 * /settings/email-domain — Custom sending domain setup.
 *
 * Guides the operator through:
 *   1. Entering their subdomain (e.g. mail.tuodominio.it)
 *   2. Getting DNS records from Resend
 *   3. Verifying propagation with one click
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { EmailDomainManager } from '@/components/email-domain-manager';
import { BentoCard } from '@/components/ui/bento-card';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { getDomainStatus } from '@/lib/data/branding';

export const dynamic = 'force-dynamic';

export default async function EmailDomainPage() {
  const [ctx, initialStatus] = await Promise.all([
    getCurrentTenantContext(),
    // Prefetch domain status for SSR paint — falls back to null if no domain or Resend unreachable.
    getDomainStatus().catch(() => null),
  ]);
  if (!ctx) redirect('/login');

  return (
    <div className="space-y-8">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          <Link href="/settings" className="hover:text-on-surface hover:underline">
            Impostazioni
          </Link>
          {' · '}Email Domain
        </p>
        <h1 className="mt-1 font-headline text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">
          Dominio mittente
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
          Invia da{' '}
          <span className="font-mono">outreach@tuodominio.it</span> invece che
          dal dominio SolarLead condiviso. Migliora deliverability, open rate e
          brand recognition.
        </p>
      </header>

      {/* Why it matters */}
      <BentoCard span="full">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Perché configurarlo
        </p>
        <div className="mt-3 grid gap-4 md:grid-cols-3">
          <WhyCard
            icon="📬"
            title="Deliverability +20%"
            desc="Le email inviate da un dominio autenticato (SPF + DKIM) vengono bloccate meno spesso dai filtri antispam."
          />
          <WhyCard
            icon="🔒"
            title="Reputazione separata"
            desc="Il tuo dominio accumula la sua reputazione. Se altri tenant mandano male non ti impattano."
          />
          <WhyCard
            icon="🏷️"
            title="Brand riconoscibile"
            desc='Il destinatario vede "Rossi Solar <outreach@rossisolar.it>" invece di un indirizzo generico.'
          />
        </div>
      </BentoCard>

      {/* Domain setup card */}
      <BentoCard span="full">
        <p className="mb-6 text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Configura dominio
        </p>
        <EmailDomainManager
          initialDomain={ctx.tenant.email_from_domain}
          initialStatus={initialStatus}
        />
      </BentoCard>
    </div>
  );
}

function WhyCard({
  icon,
  title,
  desc,
}: {
  icon: string;
  title: string;
  desc: string;
}) {
  return (
    <div className="rounded-lg bg-surface-container-low p-4">
      <div className="mb-2 text-xl">{icon}</div>
      <p className="font-semibold text-on-surface">{title}</p>
      <p className="mt-1 text-xs text-on-surface-variant">{desc}</p>
    </div>
  );
}
