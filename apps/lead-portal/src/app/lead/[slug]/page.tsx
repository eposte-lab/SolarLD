import { notFound, redirect } from 'next/navigation';
import { fetchPublicLead, formatEuro, formatYears, leadHeroCopy } from '@/lib/api';
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

  return (
    <main className="min-h-screen bg-gradient-to-b from-slate-50 to-white">
      <VisitTracker slug={slug} />
      <PortalTracker slug={slug} />

      <header
        className="bg-white shadow-sm"
        style={{ borderTop: `4px solid ${brandColor}` }}
      >
        <div className="mx-auto flex max-w-5xl items-center justify-between p-4">
          {tenant?.brand_logo_url ? (
            /* eslint-disable-next-line @next/next/no-img-element */
            <img
              src={tenant.brand_logo_url}
              alt={tenant.business_name}
              className="h-10"
            />
          ) : (
            <span className="text-lg font-semibold" style={{ color: brandColor }}>
              {tenant?.business_name ?? 'SolarLead'}
            </span>
          )}
          <span className="text-sm text-slate-500">Dossier personalizzato</span>
        </div>
      </header>

      <section className="mx-auto max-w-5xl p-6">
        <h1 className="mb-2 text-3xl font-bold">{hero.title}</h1>
        <p className="mb-6 text-slate-600">{hero.subtitle}</p>

        {lead.rendering_video_url ? (
          <video
            src={lead.rendering_video_url}
            autoPlay
            muted
            loop
            playsInline
            className="w-full rounded-lg shadow-md"
            data-portal-video
          />
        ) : lead.rendering_gif_url ? (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img
            src={lead.rendering_gif_url}
            alt="Rendering animato del tetto"
            className="w-full rounded-lg shadow-md"
          />
        ) : lead.rendering_image_url ? (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img
            src={lead.rendering_image_url}
            alt="Rendering del tetto con fotovoltaico"
            className="w-full rounded-lg shadow-md"
          />
        ) : (
          <div className="flex aspect-video items-center justify-center rounded-lg bg-slate-100 text-slate-400">
            Rendering in preparazione
          </div>
        )}
      </section>

      <section className="mx-auto max-w-5xl p-6" data-portal-roi>
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <StatCard label="Potenza installabile" value={roi.estimated_kwp ? `${roi.estimated_kwp} kWp` : '—'} color={brandColor} />
          <StatCard label="Risparmio annuo" value={formatEuro(roi.yearly_savings_eur)} color={brandColor} />
          <StatCard label="Rientro stimato" value={formatYears(roi.payback_years)} color={brandColor} />
          <StatCard
            label="CO₂ evitata (25 anni)"
            value={roi.co2_tonnes_25_years ? `${Math.round(roi.co2_tonnes_25_years)} t` : '—'}
            color={brandColor}
          />
        </div>
        <p className="mt-3 text-xs text-slate-500">
          Stime indicative basate sul consumo medio di un utente/azienda simile.
          Il preventivo formale richiede un sopralluogo gratuito.
        </p>
      </section>

      <section className="mx-auto max-w-5xl p-6">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div data-portal-cta="whatsapp">
            <WhatsAppCta
              slug={slug}
              whatsappNumber={tenant?.whatsapp_number ?? null}
              tenantName={tenant?.business_name ?? 'SolarLead'}
              brandColor={brandColor}
            />
          </div>
          <div className="rounded-lg bg-white p-6 shadow" data-portal-cta="appointment">
            <h2 className="text-lg font-semibold">Richiedi un sopralluogo</h2>
            <p className="mt-1 text-sm text-slate-600">
              Un tecnico di {tenant?.business_name ?? 'SolarLead'} vi
              ricontatterà entro 48 ore. Gratis, nessun impegno.
            </p>
            <AppointmentForm slug={slug} brandColor={brandColor} />
          </div>
        </div>
      </section>

      <footer className="mt-12 border-t border-slate-200 bg-white py-6">
        <div className="mx-auto max-w-5xl px-4 text-center text-xs text-slate-500">
          <a href={`/optout/${encodeURIComponent(slug)}`} className="underline">
            Non voglio più ricevere comunicazioni
          </a>
          {' · '}
          <a href="/privacy" className="underline">
            Privacy policy
          </a>
          {tenant?.contact_email ? (
            <>
              {' · '}
              <a href={`mailto:${tenant.contact_email}`} className="underline">
                Contatta {tenant.business_name}
              </a>
            </>
          ) : null}
        </div>
      </footer>
    </main>
  );
}

function StatCard({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color: string;
}) {
  return (
    <div className="rounded-lg bg-white p-4 shadow">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-2 text-xl font-bold" style={{ color }}>
        {value}
      </p>
    </div>
  );
}
