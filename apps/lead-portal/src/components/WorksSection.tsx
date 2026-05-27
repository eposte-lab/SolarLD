/**
 * WorksSection — "Lavori realizzati" per il lead portal.
 *
 * Portfolio/case-study del tenant: griglia di impianti realizzati con
 * foto, cliente e una riga di dettaglio (kWp · località · anno) o un
 * breve testo. In alto il numero totale di impianti come social proof.
 *
 * Si nasconde se non ci sono case study attivi. Stile coerente con
 * AboutSection: card bianca flat, accento brand sottile.
 */

import type { CaseStudy } from '@/lib/api';

type Props = {
  caseStudies: CaseStudy[];
  installationsCount: number | null;
  brandColor: string;
};

function metaLine(cs: CaseStudy): string {
  const bits: string[] = [];
  if (cs.kwp) bits.push(`${Math.round(cs.kwp * 10) / 10} kWp`);
  if (cs.location) bits.push(cs.location);
  if (cs.year) bits.push(String(cs.year));
  if (bits.length) return bits.join(' · ');
  return cs.story ?? '';
}

export function WorksSection({ caseStudies, installationsCount, brandColor }: Props) {
  const items = (caseStudies ?? []).filter((c) => c && c.client_name);
  if (items.length === 0) return null;

  return (
    <section className="mx-auto w-full max-w-5xl px-4 py-10">
      <div className="overflow-hidden rounded-3xl border border-black/[0.06] bg-white shadow-sm">
        {/* accent bar */}
        <div style={{ height: 4, backgroundColor: brandColor }} />

        <div className="px-6 py-8 sm:px-10">
          <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-neutral-400">
                Lavori realizzati
              </p>
              <h2 className="mt-1 text-2xl font-bold tracking-tight text-neutral-900">
                Impianti già installati
              </h2>
            </div>
            {installationsCount && installationsCount > 0 ? (
              <div className="text-right">
                <p
                  className="text-3xl font-extrabold leading-none"
                  style={{ color: brandColor }}
                >
                  {installationsCount.toLocaleString('it-IT')}+
                </p>
                <p className="text-[11px] uppercase tracking-wider text-neutral-400">
                  impianti realizzati
                </p>
              </div>
            ) : null}
          </div>

          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {items.map((cs, i) => (
              <article
                key={`${cs.client_name}-${i}`}
                className="overflow-hidden rounded-2xl border border-black/[0.06] bg-neutral-50"
              >
                {cs.image_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={cs.image_url}
                    alt={`Impianto realizzato per ${cs.client_name}`}
                    className="h-40 w-full object-cover"
                  />
                ) : null}
                <div className="flex items-start gap-3 p-4">
                  {cs.logo_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={cs.logo_url}
                      alt={cs.client_name}
                      className="h-8 w-8 shrink-0 rounded-md object-contain"
                    />
                  ) : null}
                  <div className="min-w-0">
                    <p className="truncate text-sm font-bold text-neutral-900">
                      {cs.client_name}
                    </p>
                    <p className="mt-0.5 text-xs text-neutral-500">{metaLine(cs)}</p>
                    {cs.story && metaLine(cs) !== cs.story ? (
                      <p className="mt-2 text-xs leading-relaxed text-neutral-600">
                        {cs.story}
                      </p>
                    ) : null}
                  </div>
                </div>
              </article>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
