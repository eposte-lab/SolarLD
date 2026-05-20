import type { Metadata } from 'next';
import { notFound, redirect } from 'next/navigation';

import { AboutSection } from '@/components/AboutSection';
import { BollettaSection } from '@/components/BollettaSection';
import { DossierExpired } from '@/components/DossierExpired';
import { EditorialHero } from '@/components/EditorialHero';
import { EpcPropositionSection } from '@/components/EpcPropositionSection';
import { HeroStat } from '@/components/HeroStat';
import { fetchPublicLead, leadHeroCopy } from '@/lib/api';

import { AppointmentForm } from './AppointmentForm';
import { PortalTracker } from './PortalTracker';
import { VisitTracker } from './VisitTracker';

type PageProps = { params: Promise<{ slug: string }> };

/** Giorni dopo l'invio oltre i quali il link del dossier scade.
 *  Il dossier è pubblico (slug non indovinabile, noindex): la scadenza
 *  limita la finestra di esposizione dei dati del prospect. */
const DOSSIER_TTL_DAYS = 30;

/** Favicon + titolo per-tenant: il portale del lead usa il logo
 *  dell'azienda cliente come favicon, così la scheda browser è
 *  brandizzata sul cliente (es. Total Trade) e non su SolarLead. */
export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const result = await fetchPublicLead(slug);
  if (result.kind !== 'ok') return {};
  const tenant = result.lead.tenant;
  const name = tenant?.business_name?.trim();
  const logo = tenant?.brand_logo_url?.trim();
  return {
    title: name
      ? `${name} — Il tuo tetto con il fotovoltaico`
      : 'Il tuo tetto con il fotovoltaico',
    ...(logo ? { icons: { icon: logo } } : {}),
  };
}

export default async function LeadPage({ params }: PageProps) {
  const { slug } = await params;
  const result = await fetchPublicLead(slug);
  if (result.kind !== 'ok') {
    if (result.kind === 'not_found') notFound();
    if (result.kind === 'gone') {
      redirect(`/optout/${encodeURIComponent(slug)}?already=1`);
    }
    // Link scaduto — l'API ha già negato i dati del lead (privacy
    // budget di DOSSIER_TTL_DAYS giorni dopo l'invio). Mostriamo la
    // pagina generica di scadenza: a 30 giorni il dossier non è più
    // riconvertibile e tenere i dati esposti non avrebbe valore.
    return (
      <DossierExpired
        tenantName="l'azienda che ti ha contattato"
        brandColor="#0F766E"
        contactEmail={null}
      />
    );
  }

  const lead = result.lead;

  // Defense-in-depth: re-controlliamo la scadenza anche client-side. Se
  // l'API risponde con i dati, ma l'età supera comunque DOSSIER_TTL_DAYS
  // (race tra clock dell'API e quello locale, o regressione), il
  // dossier non viene mostrato. Un lead non ancora contattato
  // (outreach_sent_at null) non scade: anteprima operatore.
  if (lead.outreach_sent_at) {
    const ageDays =
      (Date.now() - new Date(lead.outreach_sent_at).getTime()) / 86_400_000;
    if (ageDays > DOSSIER_TTL_DAYS) {
      return (
        <DossierExpired
          tenantName={lead.tenant?.business_name ?? 'SolarLead'}
          brandColor={lead.tenant?.brand_primary_color || '#0F766E'}
          contactEmail={lead.tenant?.contact_email ?? null}
        />
      );
    }
  }
  // ROI source priority — single source of truth (Sprint 1.1):
  //   1. roof.derivations — canonical snapshot from
  //      compute_full_derivations, refreshed when the prospect
  //      uploads a bolletta. Same numbers the dashboard inspector,
  //      email body, and preventivo PDF read from.
  //   2. lead.roi_data — legacy snapshot, fallback for leads
  //      created before migration 0094 added the derivations column.
  const { roofs: roof, tenant } = lead;
  // Defensive: derivations may be null, roi_data may be {} or null in
  // edge cases. Always end up with an empty object rather than null so
  // the rest of the rendering can do optional-chain on numeric fields.
  const roi =
    ((roof as { derivations?: typeof lead.roi_data | null } | null)?.derivations) ??
    lead.roi_data ??
    {};
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
      })} kW`,
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
            {(() => {
              // Sprint 6: il logo nell'header diventa cliccabile se il
              // tenant ha configurato il proprio sito web. Click → nuova
              // tab al sito aziendale (no rischio di mandare via il lead
              // senza preavviso).
              const logoContent = tenant?.brand_logo_url ? (
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
              );
              if (tenant?.website_url) {
                return (
                  <a
                    href={tenant.website_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="transition-opacity hover:opacity-80"
                    aria-label={`Visita il sito di ${tenantName}`}
                  >
                    {logoContent}
                  </a>
                );
              }
              return logoContent;
            })()}
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
            label="Energia prodotta dal pannello"
            value={roi.yearly_kwh ?? null}
            unit="kWh/anno"
            decimals={0}
            accentColor={brandColor}
            caption={
              // kW (no più 'kWp') come caption — il lead non tecnico
              // non sa cosa significa "picco", quindi semplifichiamo.
              // La metrica principale è la produzione annua (kWh).
              roi.estimated_kwp
                ? `${roi.estimated_kwp.toLocaleString('it-IT', { maximumFractionDigits: 1 })} kW di potenza installata`
                : null
            }
          />
          <HeroStat
            label="Risparmio annuo"
            value={
              // Prefer the realistic (sector-aware) savings when
              // available — it accounts for the lead's likely actual
              // consumption rather than treating "production × tariff"
              // as if it were all self-consumed. Falls back to the
              // legacy figure for leads still pending sector
              // classification.
              roi.realistic_yearly_savings_eur ?? roi.yearly_savings_eur ?? null
            }
            unit="€/anno"
            accentColor={brandColor}
          />
          {tenant?.epc_enabled ? (
            // Con l'EPC il cliente non investe nulla → rientro 0 anni.
            <HeroStat
              label="Rientro investimento"
              value={0}
              unit="anni"
              accentColor={brandColor}
              caption="Zero investimento con il modello EPC"
            />
          ) : (
            <HeroStat
              label="Rientro stimato"
              value={roi.payback_years ?? null}
              unit="anni"
              decimals={1}
              accentColor={brandColor}
            />
          )}
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
          Stime indicative basate sul consumo medio del settore.
          Il preventivo formale richiede un sopralluogo gratuito — caricare
          la propria bolletta affina i numeri sopra.
        </p>
      </section>

      {/* ============== EPC commercial proposition (optional) ========== */}
      {tenant?.epc_enabled && roi?.gross_capex_eur ? (
        <EpcPropositionSection
          grossCapexEur={roi.gross_capex_eur}
          brandName={tenantName}
          brandColor={brandColor}
          brandLogoUrl={tenant.brand_logo_url}
          yearlySavingsEur={roi.realistic_yearly_savings_eur ?? roi.yearly_savings_eur ?? null}
        />
      ) : null}

      {/* ============== Bolletta upload + Savings compare ============== */}
      <section className="mx-auto max-w-6xl px-6 py-6">
        <BollettaSection
          slug={slug}
          brandColor={brandColor}
          brandName={tenantName}
          epc={!!tenant?.epc_enabled}
        />
      </section>

      {/* ============== About section ============== */}
      {tenant ? (
        <section className="mx-auto max-w-6xl px-6 py-6">
          <AboutSection
            businessName={tenantName}
            brandLogoUrl={tenant.brand_logo_url}
            brandColor={brandColor}
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

      {/* ============== CTA — sopralluogo è l'unico path di contatto. ======
          La "risposta via email" è stata rimossa: il click apriva solo il
          client mail del lead e da lì il signale di reply si perdeva.
          Solo il form sopralluogo dà tracking pulito (POST tracciato →
          webhook CRM del tenant). WhatsApp è stato rimosso a monte. */}
      <section
        id="sopralluogo"
        className="mx-auto max-w-6xl scroll-mt-8 px-6 py-8"
        aria-labelledby="cta-heading"
      >
        <p className="editorial-eyebrow">Prossimo passo</p>
        <h2
          id="cta-heading"
          className="mt-2 font-headline text-2xl font-semibold tracking-tighter text-on-surface md:text-3xl"
        >
          Richiedi un sopralluogo tecnico
        </h2>
        <div className="mt-6 bento p-6">
          <p className="text-sm text-on-surface-variant">
            Un tecnico di {tenantName} vi ricontatterà entro 48 ore.
            Nessun impegno, nessun venditore.
          </p>
          <AppointmentForm
            slug={slug}
            brandColor={brandColor}
            privacyPolicyUrl={tenant?.privacy_policy_url}
            tenantName={tenantName}
          />
        </div>
      </section>

      {/* ============== Footer ============== */}
      <footer
        className="relative mt-12 overflow-hidden text-white"
        style={{ backgroundColor: brandColor }}
      >
        {/* Backdrop industriale appena percepibile: foto Unsplash di
            pannelli fotovoltaici (la precedente mostrava pale eoliche,
            wrong scope). Opacità bassa e mix-blend-screen così resta
            sotto la cortina navy senza distrarre. */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-20 mix-blend-screen"
          style={{
            backgroundImage:
              "url('https://images.unsplash.com/photo-1509391366360-2e959784a276?auto=format&fit=crop&w=2400&q=70')",
            backgroundSize: 'cover',
            backgroundPosition: 'center',
          }}
        />
        <div className="relative mx-auto max-w-6xl px-6 py-10 text-xs text-white/80">
          <div className="flex flex-col items-start gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="font-semibold text-white">
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
                className="underline hover:text-white"
              >
                Non voglio più ricevere comunicazioni
              </a>
              <a
                href={`/privacy?slug=${encodeURIComponent(slug)}`}
                className="underline hover:text-white"
              >
                Privacy policy
              </a>
              {/* "Contatta {tenant}" — punta al sito del tenant quando
                  configurato (`tenant.website_url`), altrimenti torna
                  alla vecchia mailto per i tenant senza website. */}
              {tenant?.website_url ? (
                <a
                  href={tenant.website_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline hover:text-white"
                >
                  Contatta {tenantName}
                </a>
              ) : tenant?.contact_email ? (
                <a
                  href={`mailto:${tenant.contact_email}`}
                  className="underline hover:text-white"
                >
                  Contatta {tenantName}
                </a>
              ) : null}
            </div>
          </div>
          <p className="mt-4 border-t border-white/15 pt-4 leading-relaxed">
            Questa è una pagina personale generata da {tenantName} per la
            tua azienda. I dati sono trattati per finalità commerciali B2B
            sulla base del legittimo interesse, su un link non indicizzato
            dai motori di ricerca e con scadenza automatica. Puoi opporti
            al trattamento in qualsiasi momento dal link qui sopra.
          </p>
        </div>
      </footer>
    </main>
  );
}
