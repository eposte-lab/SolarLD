import { notFound, redirect } from 'next/navigation';

import { AboutSection } from '@/components/AboutSection';
import { BehindTheNumbersPanel } from '@/components/BehindTheNumbersPanel';
import { BollettaSection } from '@/components/BollettaSection';
import { EditorialHero } from '@/components/EditorialHero';
import { EmailReplyCta } from '@/components/EmailReplyCta';
import { EpcPropositionSection } from '@/components/EpcPropositionSection';
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
          Stime indicative basate sul consumo medio del settore.
          Il preventivo formale richiede un sopralluogo gratuito — caricare
          la propria bolletta affina i numeri sopra.
        </p>
      </section>

      {/* ============== Savings hero — RISPARMIO €X/ANNO ============== */}
      {/* Inquadramento sul RISPARMIO (dato derivato dalla potenza
          dell'impianto), non sul canone "paghi solo X": non conosciamo
          la bolletta reale del cliente, quindi non promettiamo una cifra
          che fingeremmo di sapere. Card compatta. */}
      {(() => {
        const savings =
          roi.realistic_yearly_savings_eur ?? roi.yearly_savings_eur ?? null;
        if (!savings || savings <= 0) return null;
        const epc = !!tenant?.epc_enabled;

        return (
          <section className="mx-auto max-w-6xl px-6 pt-2 pb-4">
            <div
              className="relative overflow-hidden rounded-3xl p-6 md:p-8"
              style={{
                background: `linear-gradient(135deg, ${brandColor}18 0%, ${brandColor}06 60%, transparent 100%)`,
                border: `1.5px solid ${brandColor}30`,
              }}
            >
              <span
                aria-hidden
                className="absolute -right-12 -top-12 h-40 w-40 rounded-full"
                style={{
                  background: `radial-gradient(circle, ${brandColor}25 0%, transparent 70%)`,
                  animation: 'pulseGlow 3s ease-in-out infinite',
                }}
              />
              <style>{`
                @keyframes pulseGlow {
                  0%, 100% { transform: scale(1); opacity: 0.6; }
                  50%      { transform: scale(1.15); opacity: 1; }
                }
                @keyframes savingsFadeUp {
                  from { opacity: 0; transform: translateY(20px); }
                  to   { opacity: 1; transform: translateY(0); }
                }
                .savings-amount    { animation: savingsFadeUp 0.7s cubic-bezier(.22,1,.36,1) 0.1s both; }
                .savings-tagline   { animation: savingsFadeUp 0.6s cubic-bezier(.22,1,.36,1) 0.3s both; }
                .savings-cumulative{ animation: savingsFadeUp 0.6s cubic-bezier(.22,1,.36,1) 0.5s both; }
              `}</style>

              <p
                className="text-xs font-bold uppercase tracking-widest"
                style={{ color: brandColor }}
              >
                Risparmio di spese energetiche
              </p>
              <p
                className="savings-amount mt-2 font-headline text-5xl font-bold leading-none tracking-tightest md:text-6xl"
                style={{ color: brandColor }}
              >
                € {savings.toLocaleString('it-IT')}
                <span className="ml-2 text-xl font-medium text-on-surface-variant md:text-2xl">
                  /anno
                </span>
              </p>
              <p className="savings-tagline mt-3 max-w-2xl text-sm text-on-surface md:text-base">
                {epc ? (
                  <>
                    Risparmio stimato sulla potenza dell&apos;impianto
                    installabile. Con il modello EPC <strong>{tenantName}</strong>{' '}
                    non investite nulla: l&apos;impianto è nostro e a fine
                    contratto diventa vostro.
                  </>
                ) : (
                  <>
                    Risparmio stimato sulla potenza dell&apos;impianto
                    installabile. Sono spese energetiche che{' '}
                    <strong>non escono più</strong> dalla cassa.
                  </>
                )}
              </p>
              <div className="savings-cumulative mt-5 inline-flex items-center rounded-full bg-surface-container-lowest px-4 py-2.5 ring-1 ring-on-surface/5">
                <p className="text-sm text-on-surface">
                  In 25 anni{' '}
                  <strong
                    className="font-headline text-base font-bold"
                    style={{ color: brandColor }}
                  >
                    € {Math.round(savings * 25).toLocaleString('it-IT')}
                  </strong>{' '}
                  di spese energetiche risparmiate
                </p>
              </div>
            </div>
          </section>
        );
      })()}

      {/* ============== "Dietro i numeri" — calcolo trasparente ========== */}
      <BehindTheNumbersPanel
        brandColor={brandColor}
        productionKwh={roi.yearly_kwh ?? null}
        consumptionKwh={roi.estimated_consumption_kwh ?? null}
        consumptionMethod={roi.consumption_estimate_method ?? null}
        selfKwh={(roi as Record<string, unknown>).realistic_self_kwh as number | null | undefined}
        exportKwh={(roi as Record<string, unknown>).realistic_export_kwh as number | null | undefined}
        totalSavingsEur={roi.realistic_yearly_savings_eur ?? roi.yearly_savings_eur ?? null}
      />

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
        <BollettaSection slug={slug} brandColor={brandColor} brandName={tenantName} />
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
          <AppointmentForm slug={slug} brandColor={brandColor} privacyPolicyUrl={tenant?.privacy_policy_url} />
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
