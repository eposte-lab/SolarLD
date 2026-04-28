/**
 * /settings/branding — Email branding editor.
 *
 * Lets the tenant operator update brand color, logo URL, and the
 * email-from-name without re-running the onboarding wizard.
 * The live preview iframe renders the actual Jinja2 template.
 */

import {
  ArrowRight,
  BookOpenText,
  Image as ImageIcon,
  Mail,
  Palette,
  Sparkles,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
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

      <Link
        href="/settings/branding/about"
        className="group block"
      >
        <BentoCard
          variant="muted"
          padding="default"
          className="flex items-center justify-between gap-4 transition-colors hover:bg-surface-container"
        >
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/15 text-primary">
              <BookOpenText size={18} strokeWidth={1.75} aria-hidden />
            </div>
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Sezione &ldquo;Chi siamo&rdquo;
              </p>
              <p className="mt-0.5 font-semibold text-on-surface">
                Modifica narrativa, certificazioni e tagline del portale lead
              </p>
              <p className="mt-1 text-xs text-on-surface-variant">
                Il dossier che ogni cliente vede cliccando le email outreach.
              </p>
            </div>
          </div>
          <ArrowRight
            size={16}
            strokeWidth={2}
            className="shrink-0 text-on-surface-variant transition-transform group-hover:translate-x-1 group-hover:text-on-surface"
            aria-hidden
          />
        </BentoCard>
      </Link>

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
            Icon={Palette}
            title="Colore principale"
            desc="Usato per barra superiore (Classic), header gradiente (Bold) e CTA. CSS inline via Premailer."
          />
          <HowCard
            Icon={ImageIcon}
            title="Logo"
            desc="Mostrato nel header di ogni stile. Consigliato: PNG 300 × 80 px, sfondo trasparente."
          />
          <HowCard
            Icon={Mail}
            title="Nome mittente"
            desc={'Appare nell\u2019inbox come "Nome <outreach@tuodominio.it>". Configura il dominio nella sezione Email Domain.'}
          />
          <HowCard
            Icon={Sparkles}
            title="Stile & AI copy"
            desc="Scegli tra Classic, Bold e Minimal. L'AI genera headline, testo e CTA calibrati sul tuo brand."
          />
        </div>
      </BentoCard>
    </div>
  );
}

function HowCard({
  Icon,
  title,
  desc,
}: {
  Icon: LucideIcon;
  title: string;
  desc: string;
}) {
  return (
    <div className="relative overflow-hidden rounded-xl liquid-glass-sm p-4">
      <span
        className="pointer-events-none absolute inset-x-0 top-0 h-10 bg-glass-specular"
        aria-hidden
      />
      <div className="relative mb-3 flex h-9 w-9 items-center justify-center rounded-xl bg-primary/12 text-primary">
        <Icon size={16} strokeWidth={2} aria-hidden />
      </div>
      <p className="relative font-semibold text-on-surface">{title}</p>
      <p className="relative mt-1 text-xs text-on-surface-variant">{desc}</p>
    </div>
  );
}
