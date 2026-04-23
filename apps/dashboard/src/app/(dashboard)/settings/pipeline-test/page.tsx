import Link from 'next/link';
import { redirect } from 'next/navigation';

import { PipelineTestPanel } from '@/components/pipeline-test-panel';
import { BentoCard } from '@/components/ui/bento-card';
import { getCurrentTenantContext } from '@/lib/data/tenant';

export const dynamic = 'force-dynamic';

export default async function PipelineTestPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  return (
    <div className="space-y-8">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          <Link href="/settings" className="hover:text-on-surface hover:underline">
            Impostazioni
          </Link>
          {' · '}Test pipeline
        </p>
        <h1 className="mt-1 font-headline text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">
          Test pipeline end-to-end
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
          Inietta un&apos;azienda sintetica nel funnel e verifica l&apos;intero flusso:
          scoring → rendering Remotion → email via dominio verificato.
        </p>
      </header>

      <BentoCard span="full">
        <PipelineTestPanel tenantId={ctx.tenant.id} />
      </BentoCard>
    </div>
  );
}
