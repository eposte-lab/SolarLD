/**
 * /settings/branding — Email branding editor.
 *
 * Lets the tenant operator update brand color, logo URL, and the
 * email-from-name without re-running the onboarding wizard.
 * The live preview iframe renders the actual Jinja2 template.
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { BrandingEditor } from '@/components/branding-editor';
import { BentoCard } from '@/components/ui/bento-card';
import { getCurrentTenantContext } from '@/lib/data/tenant';

export const dynamic = 'force-dynamic';

export default async function BrandingPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  return (
    <div className="space-y-8">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          <Link href="/settings" className="hover:text-on-surface hover:underline">
            Impostazioni
          </Link>
          {' · '}Branding email
        </p>
        <h1 className="mt-1 font-headline text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">
          Personalizza le tue email
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
          Colore principale, logo e nome mittente vengono applicati a tutte le email outreach
          in tempo reale. Nessuna modifica ai template è necessaria.
        </p>
      </header>

      <BentoCard span="full">
        <p className="mb-6 text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Colore · Logo · Mittente
        </p>
        <BrandingEditor
          tenant={{
            id: ctx.tenant.id,
            business_name: ctx.tenant.business_name,
            brand_primary_color: ctx.tenant.brand_primary_color,
            brand_logo_url: ctx.tenant.brand_logo_url,
            email_from_name: ctx.tenant.email_from_name,
            settings: ctx.tenant.settings,
          }}
        />
      </BentoCard>

      <BentoCard span="full">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Come vengono usati
        </p>
        <div className="mt-4 grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <HowCard
            icon="🎨"
            title="Colore principale"
            desc="Usato per barra superiore (Classic), header gradiente (Bold) e CTA. CSS inline via Premailer."
          />
          <HowCard
            icon="🖼️"
            title="Logo"
            desc="Mostrato nel header di ogni stile. Consigliato: PNG 300 × 80 px, sfondo trasparente."
          />
          <HowCard
            icon="✉️"
            title="Nome mittente"
            desc={'Appare nell\u2019inbox come "Nome <outreach@tuodominio.it>". Configura il dominio nella sezione Email Domain.'}
          />
          <HowCard
            icon="🤖"
            title="Stile & AI copy"
            desc="Scegli tra Classic, Bold e Minimal. L'AI genera headline, testo e CTA calibrati sul tuo brand."
          />
        </div>
      </BentoCard>
    </div>
  );
}

function HowCard({
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
