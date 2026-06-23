import type { Metadata } from 'next';
import { notFound, redirect } from 'next/navigation';

import { fetchPublicLead } from '@/lib/api';

import { AppointmentForm } from '../AppointmentForm';
import { VisitTracker } from '../VisitTracker';

type PageProps = { params: Promise<{ slug: string }> };

/** Private, single-purpose conversion page — never indexed. */
export const metadata: Metadata = {
  title: 'Richiedi di essere ricontattato',
  robots: { index: false, follow: false },
};

/**
 * Fast "request a callback" page — the destination of the follow-up email CTA.
 *
 * The lead already saw the full dossier on the first touch, so the follow-up
 * does NOT send them back there: it lands here, on a minimal form that asks
 * only for a phone number. Submitting posts to the same appointment endpoint
 * the in-dossier form uses, so for a moderated tenant the request is held in
 * the operator's inbound queue and, on approval, emailed to the tenant.
 */
export default async function ContattoPage({ params }: PageProps) {
  const { slug } = await params;
  const result = await fetchPublicLead(slug);
  if (result.kind !== 'ok') {
    if (result.kind === 'not_found') notFound();
    // gone / expired → fall back to the dossier route, which renders the
    // appropriate opt-out / expired surface.
    redirect(`/dossier/${encodeURIComponent(slug)}`);
  }

  const { tenant } = result.lead;
  const brandColor = tenant?.brand_primary_color || '#0F766E';
  const brandAccent = tenant?.dossier_accent || tenant?.brand_color_accent || brandColor;
  const tenantName = tenant?.business_name ?? 'SolarLead';

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-5 py-10">
      <VisitTracker slug={slug} />
      <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        {tenant?.brand_logo_url ? (
          <div className="mb-5 flex justify-center">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={tenant.brand_logo_url}
              alt={tenantName}
              className="h-12 w-auto max-w-[80%] object-contain"
            />
          </div>
        ) : (
          <p
            className="mb-4 text-center text-lg font-extrabold tracking-tight"
            style={{ color: brandColor }}
          >
            {tenantName}
          </p>
        )}
        <h1 className="text-2xl font-extrabold leading-tight tracking-tight text-slate-900">
          Richiedi di essere ricontattato
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-slate-600">
          Hai già visto la tua analisi. Lasciaci un recapito e un nostro
          consulente ti richiama entro 48 ore, senza impegno.
        </p>
        <AppointmentForm
          slug={slug}
          brandColor={brandColor}
          accentColor={brandAccent}
          privacyPolicyUrl={tenant?.privacy_policy_url}
          tenantName={tenantName}
          defaultPhone={result.lead.subjects?.decision_maker_phone}
          trackContactView
        />
      </div>
    </main>
  );
}
