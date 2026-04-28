/**
 * A/B Testing — template experiments page (Part B.4, tier=enterprise).
 *
 * Each experiment tests two email subject lines against each other on
 * first-contact outreach sends. The OutreachAgent samples variants at
 * send-time; Bayesian stats (Beta-Binomial Monte Carlo) are computed
 * by the API and displayed here.
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { ExperimentsManager } from '@/components/experiments-manager';
import { BentoCard } from '@/components/ui/bento-card';
import { TierLock } from '@/components/ui/tier-lock';
import { listExperiments } from '@/lib/data/experiments';
import { getCurrentTenantContext } from '@/lib/data/tenant';

export const dynamic = 'force-dynamic';

export default async function ExperimentsPage() {
  const [ctx, rows] = await Promise.all([
    getCurrentTenantContext(),
    listExperiments(),
  ]);
  if (!ctx) redirect('/login');

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
            {' · '}A/B Testing
          </p>
          <h1 className="mt-1 font-headline text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">
            Esperimenti email
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
            Testa due oggetti email in parallelo. Il sistema assegna
            automaticamente ogni invio alla variante A o B e mostra quale
            genera più aperture con confidenza ≥95%.
          </p>
        </div>
      </header>

      {/* How it works */}
      <BentoCard span="full">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Come funziona
        </p>
        <div className="mt-3 grid gap-4 md:grid-cols-3">
          <HowItWorksCard
            step="1"
            title="Crea l'esperimento"
            description="Definisci due oggetti email e lo split (default 50/50). L'esperimento parte subito."
          />
          <HowItWorksCard
            step="2"
            title="Le email vengono inviate"
            description="L'agente di outreach assegna casualmente ogni nuovo lead alla variante A o B e registra la scelta."
          />
          <HowItWorksCard
            step="3"
            title="Dichiara il vincitore"
            description="Quando P(A vince) ≥ 95% o P(B vince) ≥ 95%, dichiara manualmente il vincitore. L'oggetto vincente diventa il default per i nuovi invii."
          />
        </div>
      </BentoCard>

      {/* Experiments list — gated to enterprise tier */}
      <TierLock
        feature="ab_testing_templates"
        tenant={ctx.tenant}
        featureLabel="A/B testing template"
      >
        <BentoCard span="full">
          <div className="mb-6">
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              I tuoi esperimenti
            </p>
            <h2 className="font-headline text-2xl font-bold tracking-tighter">
              {rows.length === 0
                ? 'Nessun esperimento ancora'
                : `${rows.length} esperiment${rows.length === 1 ? 'o' : 'i'}`}
            </h2>
          </div>
          <ExperimentsManager initialRows={rows} />
        </BentoCard>
      </TierLock>
    </div>
  );
}

function HowItWorksCard({
  step,
  title,
  description,
}: {
  step: string;
  title: string;
  description: string;
}) {
  return (
    <div className="rounded-lg bg-surface-container-low p-4">
      <div className="mb-2 flex h-7 w-7 items-center justify-center rounded-full bg-primary-container text-xs font-bold text-on-primary-container">
        {step}
      </div>
      <p className="font-semibold text-on-surface">{title}</p>
      <p className="mt-1 text-xs text-on-surface-variant">{description}</p>
    </div>
  );
}
