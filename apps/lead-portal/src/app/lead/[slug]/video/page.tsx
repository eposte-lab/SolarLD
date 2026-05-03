import Link from 'next/link';
import { notFound } from 'next/navigation';
import { fetchPublicLead } from '@/lib/api';

type PageProps = { params: Promise<{ slug: string }> };

export default async function LeadVideoPage({ params }: PageProps) {
  const { slug } = await params;

  let result;
  try {
    result = await fetchPublicLead(slug);
  } catch {
    notFound();
  }

  if (result.kind === 'not_found' || result.kind === 'gone') notFound();

  const lead = result.lead;
  const portalHref = `/lead/${slug}`;

  const displayName =
    lead.subjects?.business_name?.trim() ||
    [lead.roofs?.address, lead.roofs?.comune].filter(Boolean).join(', ') ||
    null;

  /* ── Media fallback chain (video → GIF → static after-image) ──
     If the operator has CREATIVE_SKIP_REPLICATE on (out-of-credits
     mode) or Kling failed, the video is null but we may still have
     a GIF or — most commonly — the static AFTER image (panels
     drawn on the real Solar aerial). Show whatever we have so the
     prospect doesn't land on a "video in preparazione" page when
     a perfectly good static rendering already exists. */
  const hasAnyMedia =
    Boolean(lead.rendering_video_url) ||
    Boolean(lead.rendering_gif_url) ||
    Boolean(lead.rendering_image_url);

  if (!hasAnyMedia) {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center bg-gradient-to-b from-slate-50 to-white p-8 text-center">
        <div className="max-w-md">
          <div className="mb-6 text-5xl">🎬</div>
          <h1 className="mb-3 text-2xl font-bold text-slate-800">
            Il tuo rendering è in preparazione
          </h1>
          <p className="mb-8 text-slate-600">
            Ricontrolla tra qualche ora — stiamo elaborando il rendering
            fotovoltaico personalizzato per la tua sede.
          </p>
          <Link
            href={portalHref}
            className="inline-block rounded-lg bg-teal-600 px-6 py-3 font-semibold text-white transition hover:bg-teal-700"
          >
            Torna alla simulazione
          </Link>
        </div>
      </main>
    );
  }

  /* ── Media ready (one of: video, GIF, static image) ── */
  return (
    <main className="min-h-screen bg-gradient-to-b from-slate-50 to-white">
      <div className="mx-auto max-w-2xl px-4 py-12">
        <h1 className="mb-2 text-2xl font-bold text-slate-800 md:text-3xl">
          {displayName
            ? `Il rendering del tetto di ${displayName}`
            : 'Il rendering del tuo tetto con il fotovoltaico'}
        </h1>

        <p className="mb-6 text-slate-600">
          Questo è il rendering fotovoltaico personalizzato per la vostra sede.
        </p>

        {lead.rendering_video_url ? (
          // eslint-disable-next-line jsx-a11y/media-has-caption
          <video
            src={lead.rendering_video_url}
            poster={
              lead.rendering_gif_url ?? lead.rendering_image_url ?? undefined
            }
            controls
            autoPlay
            muted
            loop
            playsInline
            className="w-full max-w-2xl rounded-xl shadow-lg"
          />
        ) : lead.rendering_gif_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={lead.rendering_gif_url}
            alt="Rendering animato del tetto"
            className="w-full max-w-2xl rounded-xl shadow-lg"
          />
        ) : (
          // Static after-image fallback. Same artefact the email body
          // uses when CREATIVE_SKIP_REPLICATE bypasses the video step.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={lead.rendering_image_url!}
            alt="Foto del tetto con pannelli (statica)"
            className="w-full max-w-2xl rounded-xl shadow-lg"
          />
        )}

        <p className="mt-4 text-sm text-slate-500">
          Questo è il rendering fotovoltaico personalizzato per la vostra sede.
        </p>

        <div className="mt-8 flex flex-col gap-3 sm:flex-row">
          <Link
            href={portalHref}
            className="flex-1 rounded-lg bg-teal-600 px-6 py-3 text-center font-semibold text-white transition hover:bg-teal-700"
          >
            Richiedi un preventivo
          </Link>
          <Link
            href={portalHref}
            className="flex-1 rounded-lg border border-slate-300 bg-white px-6 py-3 text-center font-semibold text-slate-700 transition hover:bg-slate-50"
          >
            Torna alla simulazione
          </Link>
        </div>
      </div>
    </main>
  );
}
