import { notFound, redirect } from 'next/navigation';

import { AboutSection } from '@/components/AboutSection';
import { BollettaSection } from '@/components/BollettaSection';
import { EditorialHero } from '@/components/EditorialHero';
import { EmailReplyCta } from '@/components/EmailReplyCta';
import { HeroStat } from '@/components/HeroStat';
import { fetchPublicLead, leadHeroCopy } from '@/lib/api';

import { AppointmentForm } from './AppointmentForm';
import { PortalTracker } from './PortalTracker';
import { VisitTracker } from './VisitTracker';
import { WhatsAppCta } from './WhatsAppCta';

type PageProps = { params: Promise<{ slug: string }> };

export default async function LeadPage({ params }: PageProps) {
  const { slug } = await params;
  const result = await fetchPublicLead(slug);
  if (result.kind === 'not_found') notFound();
  if (result.kind === 'gone') redirect(`/optout/${encodeURIComponent(slug)}?already=1`);

  const lead = result.lead;
  const { roofs: roof, tenant, roi_data: roi } = lead;
  const hero = leadHeroCopy(lead);
  const brandColor = tenant?.brand_primary_color || '#0F766E';
  const tenantName = tenant?.business_name ?? 'SolarLead';

  // Pre-compute the technical specs grid — only rendered if at least
  // one value is available, so we don't show an empty placeholder
  // section on leads where Hunter couldn't pin the roof footprint.
  const techSpecs: { label: string; value: string }[] = [];
  if (roof?.area_sqm) {
    techSpecs.push({
      label: 'Superficie tetto utile',
      value: `${Math.round(roof.area_sqm).toLocaleString('it-IT')} m²`,
    });
  }
  if (roof?.estimated_kwp) {
    techSpecs.push({
      label: 'Potenza installabile',
      value: `${roof.estimated_kwp.toLocaleString('it-IT', {
        maximumFractionDigits: 1,
      })} kWp`,
    });
  }
  if (roof?.estimated_yearly_kwh) {
    techSpecs.push({
      label: 'Produzione annua stimata',
      value: `${Math.round(roof.estimated_yearly_kwh).toLocaleString('it-IT')} kWh`,
    });
  }
  if (
    roof?.address ||
    roof?.cap ||
    roof?.comune ||
    roof?.provincia
  ) {
    const location = [roof?.address, roof?.cap, roof?.comune, roof?.provincia]
      .filter(Boolean)
      .join(', ');
    techSpecs.push({ label: 'Sede analizzata', value: location });
  }

  return (
    <main className="min-h-screen bg-surface text-on-surface">
      <VisitTracker slug={slug} />
      <PortalTracker slug={slug} />

      {/* ============== Header ============== */}
      <header className="bg-surface-container">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
          <div className="flex items-center gap-3">
            {tenant?.brand_logo_url ? (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img
                src={tenant.brand_logo_url}
                alt={tenantName}
                className="h-10 w-auto"
              />
            ) : (
              <span
                className="font-headline text-xl font-semibold tracking-tighter"
                style={{ color: brandColor }}
              >
                {tenantName}
              </span>
            )}
            {tenant?.about_tagline ? (
              <span className="hidden border-l border-outline-variant pl-3 text-xs text-on-surface-variant md:block">
                {tenant.about_tagline}
              </span>
            ) : null}
          </div>
          <span className="editorial-eyebrow hidden md:inline">
            Dossier personalizzato
          </span>
        </div>
        <div
          className="h-1.5 w-full"
          style={{ backgroundColor: brandColor }}
          aria-hidden
        />
      </header>

      {/* ============== Hero copy + video ============== */}
      <section className="mx-auto max-w-6xl px-6 pt-10 pb-6">
        <p className="editorial-eyebrow">Proposta fotovoltaica</p>
        <h1 className="mt-3 font-headline text-3xl font-semibold tracking-tightest text-on-surface md:text-5xl">
          {hero.title}
        </h1>
        <p className="mt-3 max-w-3xl text-base text-on-surface-variant md:text-lg">
          {hero.subtitle}
        </p>

        <div className="mt-8">
          <EditorialHero
            slug={slug}
            videoUrl={lead.rendering_video_url}
            gifUrl={lead.rendering_gif_url}
            imageUrl={lead.rendering_image_url}
            posterUrl={lead.rendering_image_url}
            brandColor={brandColor}
          />
        </div>
      </section>

      {/* ============== ROI strip ============== */}
      <section
        className="mx-auto max-w-6xl px-6 py-6"
        data-portal-roi
        aria-labelledby="roi-heading"
      >
        <h2 id="roi-heading" className="sr-only">
          Indicatori chiave di ritorno
        </h2>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <HeroStat
            label="Potenza installabile"
            value={roi.estimated_kwp ?? null}
            unit="kWp"
            decimals={1}
            accentColor={brandColor}
          />
          <HeroStat
            label="Risparmio annuo"
            value={roi.yearly_savings_eur ?? null}
            unit="€/anno"
            accentColor={brandColor}
          />
          <HeroStat
            label="Rientro stimato"
            value={roi.payback_years ?? null}
            unit="anni"
            decimals={1}
            accentColor={brandColor}
          />
          <HeroStat
            label="CO₂ evitata (25 anni)"
            value={
              roi.co2_tonnes_25_years
                ? Math.round(roi.co2_tonnes_25_years)
                : null
            }
            unit="tonnellate"
            accentColor={brandColor}
          />
        </div>
        <p className="mt-3 text-xs text-on-surface-muted">
          Stime indicative basate sul consumo medio di un cliente simile.
          Il preventivo formale richiede un sopralluogo gratuito — caricare
          la propria bolletta affina i numeri sopra.
        </p>
      </section>

      {/* ============== Bolletta upload + Savings compare ============== */}
      <section className="mx-auto max-w-6xl px-6 py-6">
        <BollettaSection slug={slug} brandColor={brandColor} />
      </section>

      {/* ============== About section ============== */}
      {tenant ? (
        <section className="mx-auto max-w-6xl px-6 py-6">
          <AboutSection
            businessName={tenantName}
            tagline={tenant.about_tagline}
            aboutMd={tenant.about_md}
            yearFounded={tenant.about_year_founded}
            teamSize={tenant.about_team_size}
            certifications={tenant.about_certifications}
            heroImageUrl={tenant.about_hero_image_url}
          />
        </section>
      ) : null}

      {/* ============== Technical specs ============== */}
      {techSpecs.length > 0 ? (
        <section className="mx-auto max-w-6xl px-6 py-6">
          <p className="editorial-eyebrow">Specifiche tecniche</p>
          <h2 className="mt-2 font-headline text-2xl font-semibold tracking-tighter text-on-surface md:text-3xl">
            Cosa diciamo del vostro tetto
          </h2>
          <div className="mt-6 grid grid-cols-1 gap-3 md:grid-cols-2">
            {techSpecs.map((spec) => (
              <div key={spec.label} className="bento p-5">
                <p className="editorial-eyebrow">{spec.label}</p>
                <p className="mt-2 font-headline text-xl font-semibold tracking-tighter text-on-surface">
                  {spec.value}
                </p>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {/* ============== Dual CTA ============== */}
      <section
        className="mx-auto max-w-6xl px-6 py-8"
        aria-labelledby="cta-heading"
      >
        <p className="editorial-eyebrow">Prossimo passo</p>
        <h2
          id="cta-heading"
          className="mt-2 font-headline text-2xl font-semibold tracking-tighter text-on-surface md:text-3xl"
        >
          Parliamone come preferisci
        </h2>
        <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          <div data-portal-cta="whatsapp" className="bento p-1.5">
            {/* WhatsApp = primario: card grande, brand color, prima posizione */}
            <WhatsAppCta
              slug={slug}
              whatsappNumber={tenant?.whatsapp_number ?? null}
              tenantName={tenantName}
              brandColor={brandColor}
            />
          </div>
          <EmailReplyCta
            slug={slug}
            contactEmail={tenant?.contact_email ?? null}
            tenantName={tenantName}
            heroTitle={hero.title}
            brandColor={brandColor}
          />
        </div>

        <div
          className="mt-4 bento p-6"
          data-portal-cta="appointment"
          aria-labelledby="appointment-heading"
        >
          <h3
            id="appointment-heading"
            className="font-headline text-lg font-semibold text-on-surface"
          >
            Preferisci un sopralluogo gratuito?
          </h3>
          <p className="mt-1 text-sm text-on-surface-variant">
            Un tecnico di {tenantName} vi ricontatterà entro 48 ore.
            Nessun impegno, nessun venditore.
          </p>
          <AppointmentForm slug={slug} brandColor={brandColor} />
        </div>
      </section>

      {/* ============== Footer ============== */}
      <footer className="mt-12 border-t border-outline-variant bg-surface-container">
        <div className="mx-auto max-w-6xl px-6 py-8 text-xs text-on-surface-muted">
          <div className="flex flex-col items-start gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="font-medium text-on-surface-variant">
                {tenant?.legal_name ?? tenantName}
              </p>
              <p className="mt-1">
                {[
                  tenant?.vat_number ? `P.IVA ${tenant.vat_number}` : null,
                  tenant?.legal_address,
                ]
                  .filter(Boolean)
                  .join(' · ') || 'Dati legali non configurati'}
              </p>
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              <a
                href={`/optout/${encodeURIComponent(slug)}`}
                className="underline hover:text-on-surface-variant"
              >
                Non voglio più ricevere comunicazioni
              </a>
              <a
                href="/privacy"
                className="underline hover:text-on-surface-variant"
              >
                Privacy policy
              </a>
              {tenant?.contact_email ? (
                <a
                  href={`mailto:${tenant.contact_email}`}
                  className="underline hover:text-on-surface-variant"
                >
                  Contatta {tenantName}
                </a>
              ) : null}
            </div>
          </div>
        </div>
      </footer>
    </main>
  );
}
