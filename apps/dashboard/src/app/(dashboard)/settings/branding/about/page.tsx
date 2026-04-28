/**
 * /settings/branding/about — Edit "Chi siamo" narrative.
 *
 * Sprint 8 Fase A.2. Backed by `tenants.about_*` columns (migration
 * 0064_tenant_about.sql) and the `/v1/branding/about` endpoints. The
 * values rendered here are surfaced on every public lead portal page
 * via `<AboutSection>` (Fase A.3) — they're the dossier-feel pitch
 * that tells the lead who they're talking to.
 */

import { Info } from 'lucide-react';
import Link from 'next/link';
import { redirect } from 'next/navigation';

import { AboutEditor } from '@/components/branding/about-editor';
import type { AboutEditorValues } from '@/components/branding/about-editor';
import { BentoCard } from '@/components/ui/bento-card';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { createSupabaseServerClient } from '@/lib/supabase/server';

export const dynamic = 'force-dynamic';

async function loadAbout(tenantId: string): Promise<AboutEditorValues> {
  const supabase = await createSupabaseServerClient();
  const { data } = await supabase
    .from('tenants')
    .select(
      'about_md, about_year_founded, about_team_size, about_certifications, about_hero_image_url, about_tagline',
    )
    .eq('id', tenantId)
    .limit(1)
    .maybeSingle();

  return {
    about_md: (data?.about_md as string | null) ?? null,
    about_year_founded: (data?.about_year_founded as number | null) ?? null,
    about_team_size: (data?.about_team_size as string | null) ?? null,
    about_certifications: Array.isArray(data?.about_certifications)
      ? (data?.about_certifications as string[])
      : [],
    about_hero_image_url: (data?.about_hero_image_url as string | null) ?? null,
    about_tagline: (data?.about_tagline as string | null) ?? null,
  };
}

export default async function BrandingAboutPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const initial = await loadAbout(ctx.tenant.id);

  return (
    <div className="space-y-8">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          <Link href="/settings" className="hover:text-on-surface hover:underline">
            Impostazioni
          </Link>
          {' · '}
          <Link href="/settings/branding" className="hover:text-on-surface hover:underline">
            Branding
          </Link>
          {' · '}Chi siamo
        </p>
        <h1 className="mt-1 font-headline text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">
          Chi siamo
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
          La narrativa qui sotto compare nella sezione Chi siamo del portale lead
          pubblico — la pagina che ogni cliente apre cliccando il CTA delle email.
        </p>
      </header>

      <BentoCard variant="muted" padding="default">
        <div className="flex items-start gap-3 text-sm text-on-surface-variant">
          <Info size={16} strokeWidth={1.75} className="mt-0.5 shrink-0 text-primary" />
          <p>
            Il Markdown supporta titoli (<code className="font-mono text-xs">## …</code>),
            grassetto (<code className="font-mono text-xs">**…**</code>) ed elenchi puntati.
            Nessun HTML inline: il rendering è sanificato lato portale.
          </p>
        </div>
      </BentoCard>

      <BentoCard span="full">
        <p className="mb-6 text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Identità · Narrativa · Certificazioni
        </p>
        <AboutEditor initial={initial} />
      </BentoCard>
    </div>
  );
}
