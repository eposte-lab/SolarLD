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
      className="relative overflow-hidden rounded-3xl text-white shadow-ambient"
      style={{ backgroundColor: brandColor }}
      aria-labelledby="about-heading"
    >
      {/* Semicerchio bianco che sporge dal lato destro della card e
          logo del tenant sopra di esso. Entrambi sono posizionati
          absolute sulla section e ancorati a `top-1/2 -translate-y-1/2`,
          così rimangono SEMPRE allineati al centro verticale della card,
          indipendentemente da quanto è alta la narrativa "Chi siamo". Il
          wordmark di Total Trade è blu navy: senza il disco bianco non
          si leggerebbe sul fondo brand. Su mobile niente disco: il logo
          va in un blocco con pill bianca in cima al contenuto. */}
      {brandLogoUrl ? (
        <>
          <div
            aria-hidden
            className="pointer-events-none absolute right-0 top-1/2 hidden h-80 w-80 -translate-y-1/2 translate-x-1/3 rounded-full bg-white md:block"
          />
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={brandLogoUrl}
            alt={businessName}
            className="pointer-events-none absolute right-12 top-1/2 z-10 hidden h-20 w-auto -translate-y-1/2 md:block md:h-24 lg:h-28"
          />
        </>
      ) : null}

      {/* Padding destro extra su desktop per non far entrare il testo
          sotto al disco/logo (il disco visibile è largo ~213 px). */}
      <div className="relative p-7 md:p-10 md:pr-64">
        <p className="text-[11px] font-bold uppercase tracking-[0.2em] text-white/70">
          Chi siamo
        </p>

        {/* Mobile: logo su pill bianca in alto. Desktop: il logo
            assoluto qui sopra ha già il suo posto sul disco. */}
        {brandLogoUrl ? (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img
            src={brandLogoUrl}
            alt={businessName}
            className="mt-4 inline-block h-12 max-w-[260px] rounded-xl bg-white px-3 py-2 md:hidden"
          />
        ) : (
          <h2
            id="about-heading"
            className="mt-3 font-headline text-2xl font-semibold tracking-tighter text-white md:text-3xl"
          >
            {businessName}
          </h2>
        )}

        {tagline ? (
          <p className="mt-3 max-w-2xl text-lg leading-snug text-white md:text-xl">
            {tagline}
          </p>
        ) : null}

        {heroImageUrl ? (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img
            src={heroImageUrl}
            alt={`Foto di ${businessName}`}
            className="mt-6 h-48 w-full rounded-2xl object-cover md:h-64"
          />
        ) : null}

        {aboutMd ? (
          <div className="prose-editorial mt-6 text-white/90 [&_a]:text-white [&_a]:underline [&_strong]:text-white">
            <Markdown rehypePlugins={[rehypeSanitize]}>{aboutMd}</Markdown>
          </div>
        ) : null}

        {chips.length > 0 ? (
          <ul className="mt-6 flex flex-wrap gap-2">
            {chips.map((chip) => (
              <li
                key={chip.key}
                className="inline-flex items-center rounded-full bg-white/15 px-3.5 py-1.5 text-xs font-semibold text-white"
              >
                {chip.label}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  );
}
