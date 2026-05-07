/**
 * Esperimenti A/B — top-level page (Sprint 9 Fase B/C surfacing).
 *
 * Wraps the ClusterAbPanel that was previously buried under
 * /settings/email-template (tab "A/B Test"). The panel itself is a
 * client component so we just hydrate it with the initial cluster
 * list fetched server-side.
 *
 * Why a dedicated route instead of a deep-link to the settings tab:
 * the operator runs A/B testing as an everyday operational concern
 * (which copy is winning, which clusters need attention) — burying
 * it under "Settings" hides the autonomous loop the customer is
 * supposed to admire on the demo. Top-level menu placement matches
 * the importance of the feature.
 */

import { redirect } from 'next/navigation';

import { ClusterAbPanel } from '@/components/email-template/cluster-ab-panel';
import { BentoCard } from '@/components/ui/bento-card';
import { listActiveClusters } from '@/lib/data/cluster-ab';
import { getCurrentTenantContext } from '@/lib/data/tenant';

export const dynamic = 'force-dynamic';

export default async function AbTestingPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  // listActiveClusters is a fetch wrapper — failures must not crash
  // the page. Operators want to see the explainer + empty state on a
  // brand-new tenant just as much as the data on a loaded one.
  let clusters: Awaited<ReturnType<typeof listActiveClusters>>['clusters'] = [];
  try {
    const res = await listActiveClusters();
    clusters = res.clusters;
  } catch {
    clusters = [];
  }

  return (
    <div className="space-y-6">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Esperimenti A/B · {clusters.length} cluster attiv{clusters.length === 1 ? 'o' : 'i'}
        </p>
        <h1 className="font-headline text-4xl font-bold tracking-tighter">
          Esperimenti A/B
        </h1>
        <p className="mt-2 max-w-3xl text-sm text-on-surface-variant">
          Per ogni cluster di lead simili (settore + ruolo decisore + dimensione)
          il sistema mantiene due varianti email — A e B — assegnate
          automaticamente 50/50. Ogni notte alle 03:30 controlla con un
          chi-square test se una delle due ha vinto. Quando una vince,
          Haiku ne genera una nuova sfidante. Il loop è autonomo.
        </p>
      </header>

      {/* "Come funziona" strip */}
      <BentoCard span="full">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Come funziona
        </p>
        <div className="mt-3 grid gap-4 md:grid-cols-3">
          <HowItWorksCard
            step="1"
            title="Cluster automatici"
            description="Ogni lead riceve una firma tipo automotive_ceo o horeca_owner. Lead simili competono nello stesso esperimento."
          />
          <HowItWorksCard
            step="2"
            title="Assegnazione stabile 50/50"
            description="L'agente di outreach assegna deterministicamente ogni lead ad A o B (basato sull'hash del lead_id). Lo stesso lead riceve sempre la stessa variante anche per i follow-up."
          />
          <HowItWorksCard
            step="3"
            title="Vincitore + nuova sfidante"
            description="Chi-square notturno con soglia 20 invii/variante. Vincitore promosso, perdente demoted, Haiku rigenera una nuova B per continuare il test."
          />
        </div>
      </BentoCard>

      <BentoCard span="full">
        <ClusterAbPanel initialClusters={clusters} />
      </BentoCard>
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
      <p className="mt-1 text-xs text-on-surface-variant leading-relaxed">
        {description}
      </p>
    </div>
  );
}
