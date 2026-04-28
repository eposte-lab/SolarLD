/**
 * AboutSection — "Chi siamo" card per il lead portal.
 *
 * Sprint 8 Fase A.2/A.3. Renderizza la narrativa che il tenant edita
 * in dashboard /settings/branding/about. Il markdown passa per
 * ``rehype-sanitize`` (whitelist tag/attribute, nessun ``<script>``) —
 * il portale è pubblico quindi non possiamo fidarci ciecamente del
 * contenuto, anche se viene da un utente autenticato.
 *
 * Layout: hero image opzionale a sinistra, copy + chip a destra. Su
 * mobile lo stack diventa verticale (image sopra). Se nessun campo
 * about_* è popolato, ritorniamo ``null`` — il lead vede solo le altre
 * sezioni del dossier.
 */

import Markdown from 'react-markdown';
import rehypeSanitize from 'rehype-sanitize';

type Props = {
  businessName: string;
  tagline: string | null;
  aboutMd: string | null;
  yearFounded: number | null;
  teamSize: string | null;
  certifications: string[] | null;
  heroImageUrl: string | null;
};

export function AboutSection({
  businessName,
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
    <section className="bento-glass p-6 md:p-8" aria-labelledby="about-heading">
      <p className="editorial-eyebrow">Chi siamo</p>
      <h2
        id="about-heading"
        className="mt-2 font-headline text-2xl font-semibold tracking-tighter text-on-surface md:text-3xl"
      >
        {businessName}
      </h2>
      {tagline ? (
        <p className="mt-2 text-base text-on-surface-variant md:text-lg">
          {tagline}
        </p>
      ) : null}

      <div className="mt-6 grid gap-6 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        {heroImageUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={heroImageUrl}
            alt={`Foto di ${businessName}`}
            className="h-full w-full rounded-2xl object-cover shadow-ambient-sm"
          />
        ) : null}

        <div>
          {aboutMd ? (
            <div className="prose-editorial text-on-surface">
              <Markdown rehypePlugins={[rehypeSanitize]}>{aboutMd}</Markdown>
            </div>
          ) : null}

          {chips.length > 0 ? (
            <ul className="mt-5 flex flex-wrap gap-2">
              {chips.map((chip) => (
                <li
                  key={chip.key}
                  className="inline-flex items-center rounded-full bg-surface-container-high px-3 py-1 text-xs font-medium text-on-surface-variant"
                >
                  {chip.label}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </section>
  );
}
