import { notFound } from 'next/navigation';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

type PageProps = { params: Promise<{ slug: string }> };

type PublicLead = {
  public_slug: string;
  score: number;
  score_tier: 'hot' | 'warm' | 'cold' | 'rejected';
  rendering_image_url: string | null;
  rendering_video_url: string | null;
  rendering_gif_url: string | null;
  roi_data: {
    investment_eur?: number;
    yearly_savings_eur?: number;
    payback_years?: number;
  };
  subjects: {
    type: 'b2b' | 'b2c' | 'unknown';
    business_name?: string | null;
    owner_first_name?: string | null;
  } | null;
  roofs: {
    address?: string | null;
    cap?: string | null;
    comune?: string | null;
    area_sqm?: number | null;
    estimated_kwp?: number | null;
    estimated_yearly_kwh?: number | null;
  } | null;
  tenant: {
    business_name: string;
    brand_logo_url: string | null;
    brand_primary_color: string;
    whatsapp_number: string | null;
  } | null;
};

async function fetchLead(slug: string): Promise<PublicLead | null> {
  const res = await fetch(`${API_URL}/v1/public/lead/${encodeURIComponent(slug)}`, {
    next: { revalidate: 3600 },
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Failed to load lead: ${res.status}`);
  return res.json() as Promise<PublicLead>;
}

export default async function LeadPage({ params }: PageProps) {
  const { slug } = await params;
  const lead = await fetchLead(slug);
  if (!lead) notFound();

  const { roofs: roof, tenant, roi_data: roi } = lead;

  return (
    <main className="min-h-screen bg-gradient-to-b from-slate-50 to-white">
      <header className="bg-white shadow-sm">
        <div className="mx-auto flex max-w-5xl items-center justify-between p-4">
          {tenant?.brand_logo_url ? (
            /* eslint-disable-next-line @next/next/no-img-element */
            <img src={tenant.brand_logo_url} alt={tenant.business_name} className="h-10" />
          ) : (
            <span className="text-lg font-semibold text-brand">{tenant?.business_name}</span>
          )}
          <span className="text-sm text-slate-500">Dossier personalizzato</span>
        </div>
      </header>

      <section className="mx-auto max-w-5xl p-6">
        <h1 className="mb-2 text-3xl font-bold">Ecco come potrebbe essere il tuo tetto</h1>
        <p className="mb-6 text-slate-600">
          {roof?.address} · {roof?.comune} ({roof?.cap})
        </p>

        {lead.rendering_video_url ? (
          <video
            src={lead.rendering_video_url}
            autoPlay
            muted
            loop
            playsInline
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

      <section className="mx-auto max-w-5xl p-6">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <div className="rounded-lg bg-white p-6 shadow">
            <p className="text-xs uppercase text-slate-500">Investimento stimato</p>
            <p className="mt-2 text-3xl font-bold text-brand">
              {roi.investment_eur ? `€ ${roi.investment_eur.toLocaleString('it-IT')}` : '—'}
            </p>
          </div>
          <div className="rounded-lg bg-white p-6 shadow">
            <p className="text-xs uppercase text-slate-500">Risparmio annuo</p>
            <p className="mt-2 text-3xl font-bold text-brand">
              {roi.yearly_savings_eur ? `€ ${roi.yearly_savings_eur.toLocaleString('it-IT')}` : '—'}
            </p>
          </div>
          <div className="rounded-lg bg-white p-6 shadow">
            <p className="text-xs uppercase text-slate-500">Payback</p>
            <p className="mt-2 text-3xl font-bold text-brand">
              {roi.payback_years ? `${roi.payback_years.toFixed(1)} anni` : '—'}
            </p>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-5xl p-6">
        {tenant?.whatsapp_number ? (
          <a
            href={`https://wa.me/${tenant.whatsapp_number.replace(/\D/g, '')}`}
            className="block w-full rounded-lg bg-green-600 p-4 text-center text-xl font-semibold text-white shadow hover:bg-green-700"
          >
            💬 Parla con {tenant.business_name} su WhatsApp
          </a>
        ) : (
          <div className="rounded-lg bg-slate-100 p-4 text-center text-slate-500">
            Contatto WhatsApp non ancora configurato.
          </div>
        )}
      </section>

      <footer className="mt-12 border-t border-slate-200 bg-white py-6">
        <div className="mx-auto max-w-5xl px-4 text-center text-xs text-slate-500">
          <a href={`/optout/${lead.public_slug}`} className="underline">
            Non voglio più ricevere comunicazioni
          </a>
          {' · '}
          <a href="/privacy" className="underline">
            Privacy policy
          </a>
        </div>
      </footer>
    </main>
  );
}
