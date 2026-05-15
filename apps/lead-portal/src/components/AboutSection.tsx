/**
 * AboutSection — "Chi siamo" card per il lead portal.
 *
 * Renderizza la narrativa che il tenant edita in dashboard
 * /settings/branding/about. Il markdown passa per ``rehype-sanitize``
 * (whitelist tag/attribute, nessun ``<script>``) — il portale è
 * pubblico quindi non ci fidiamo ciecamente del contenuto.
 *
 * Stile ispirato a totaltrade.it: card bianca flat, molto respiro,
 * logo aziendale in evidenza, barra d'accento sottile nel colore brand.
 */

import Markdown from 'react-markdown';
import rehypeSanitize from 'rehype-sanitize';

type Props = {
  businessName: string;
  brandLogoUrl: string | null;
  brandColor: string;
  tagline: string | null;
  aboutMd: string | null;
  yearFounded: number | null;
  teamSize: string | null;
  certifications: string[] | null;
  heroImageUrl: string | null;
};

export function AboutSection({
  businessName,
  brandLogoUrl,
  brandColor,
  tagline,
  aboutMd,
  yearFounded,
  teamSize,
  certifications,
  heroImageUrl,
}: Props) {
  const certs = (certifications ?? []).filter((c) => c && c.trim());
  const hasAnyContent =
    tagline ||
    aboutMd ||
    yearFounded ||
    teamSize ||
    certs.length > 0 ||
    heroImageUrl;
  if (!hasAnyContent) return null;

  const chips: { key: string; label: string }[] = [];
  if (yearFounded) {
    chips.push({ key: 'year', label: `Dal ${yearFounded}` });
  }
  if (teamSize) {
    chips.push({ key: 'team', label: `${teamSize} persone` });
  }
  for (const cert of certs.slice(0, 6)) {
    chips.push({ key: `cert-${cert}`, label: cert });
  }

  return (
    <section
      className="overflow-hidden rounded-3xl bg-surface-container-lowest shadow-ambient"
      aria-labelledby="about-heading"
    >
      {/* Barra d'accento brand in alto — flat, stile sito Total Trade. */}
      <div className="h-1.5 w-full" style={{ backgroundColor: brandColor }} />

      <div className="p-7 md:p-10">
        {/* Header: logo aziendale in evidenza. */}
        <div className="flex items-center justify-between gap-4">
          <p
            className="text-[11px] font-bold uppercase tracking-[0.2em]"
            style={{ color: brandColor }}
          >
            Chi siamo
          </p>
          {brandLogoUrl ? (
            /* eslint-disable-next-line @next/next/no-img-element */
            <img
              src={brandLogoUrl}
              alt={businessName}
              className="h-12 w-auto md:h-14"
            />
          ) : null}
        </div>

        {/* Titolo: il nome azienda solo se non c'è già il logo wordmark. */}
        {brandLogoUrl ? null : (
          <h2
            id="about-heading"
            className="mt-3 font-headline text-2xl font-semibold tracking-tighter text-on-surface md:text-3xl"
          >
            {businessName}
          </h2>
        )}
        {tagline ? (
          <p className="mt-3 max-w-2xl text-lg leading-snug text-on-surface md:text-xl">
            {tagline}
          </p>
        ) : null}

        <div className="mt-7 grid gap-7 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
          {heroImageUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={heroImageUrl}
              alt={`Foto di ${businessName}`}
              className="h-full w-full rounded-2xl object-cover"
            />
          ) : null}

          <div className={heroImageUrl ? '' : 'md:col-span-2'}>
            {aboutMd ? (
              <div className="prose-editorial text-on-surface-variant">
                <Markdown rehypePlugins={[rehypeSanitize]}>{aboutMd}</Markdown>
              </div>
            ) : null}

            {chips.length > 0 ? (
              <ul className="mt-6 flex flex-wrap gap-2">
                {chips.map((chip) => (
                  <li
                    key={chip.key}
                    className="inline-flex items-center rounded-full px-3.5 py-1.5 text-xs font-semibold"
                    style={{
                      backgroundColor: `${brandColor}12`,
                      color: brandColor,
                    }}
                  >
                    {chip.label}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
