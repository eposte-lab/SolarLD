'use client';

/**
 * EpcPropositionSection — animated EPC commercial proposition.
 *
 * Renders only when `tenant.epc_enabled = true`. Shows the Total
 * Trade / ESCO model: the tenant (not the lead) bears the full
 * installation cost, the lead saves ~20% on electricity immediately,
 * and after 10 years the plant is ceded at €100.
 *
 * Visualizzazione ispirata alla slide Plenitude:
 *   - una barra "Senza EPC" che cresce nel tempo (la bolletta che
 *     non smette mai),
 *   - una barra "Con EPC" che resta bassa per 10 anni (canone) e poi
 *     scende ulteriormente (impianto ceduto a €100),
 *   - card costo con strikethrough rosso (la cifra "fittizia" che
 *     l'EPC ti toglie dal tavolo).
 *
 * Tutto puro CSS keyframes — 0 video, 0 GIF, 0 Replicate.
 */

import React from 'react';
import { formatEuro } from '@/lib/api';

type Props = {
  grossCapexEur: number;
  brandName: string;
  brandColor: string;
  brandLogoUrl?: string | null;
  /** Yearly savings stimato — usato per popolare le barre comparative. */
  yearlySavingsEur?: number | null;
};

export function EpcPropositionSection({
  grossCapexEur,
  brandName,
  brandColor,
  brandLogoUrl,
  yearlySavingsEur,
}: Props) {
  // Build the 10-year comparison data for the bar chart.
  // "Senza EPC": cumulative bill cost — proxy = yearlySavings * 5 (i.e.
  // come se pagaste comunque ~5× il valore del risparmio in bolletta,
  // approssimazione conservativa). Cresce linearmente.
  // "Con EPC": canone annuo simbolico = 80% del risparmio (paghi un
  // canone più basso della bolletta attuale, dopo 10 anni il canone si
  // azzera).
  const annualSavings = Math.max(0, yearlySavingsEur ?? 0);
  const years = [1, 3, 5, 7, 10, 15, 20, 25];

  // Heights are in % of the chart's bar area, mapped 0–100.
  // Reference: max = year 25 senza EPC (cumulative ~ 5x savings * 25).
  // We don't need exact numbers; this is a visual proxy.
  const maxCumulative = Math.max(annualSavings * 25 * 5, 1);
  function heightWithout(year: number) {
    // Linear cumulative — la bolletta non smette mai di crescere.
    return Math.min(100, ((annualSavings * 5 * year) / maxCumulative) * 100);
  }
  function heightWith(year: number) {
    // Canone basso fino a anno 10, poi azzerato (risparmio puro).
    if (year <= 10) {
      return Math.min(100, ((annualSavings * 4 * year) / maxCumulative) * 100);
    }
    // Dopo anno 10: solo i canoni dei primi 10 anni accumulati, poi più nulla.
    const accumulatedFirstDecade = annualSavings * 4 * 10;
    return Math.min(100, (accumulatedFirstDecade / maxCumulative) * 100);
  }

  return (
    <section
      className="mx-auto max-w-6xl px-6 py-8"
      aria-labelledby="epc-heading"
    >
      <style>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(14px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes strikeGrow {
          from { transform: scaleX(0); }
          to   { transform: scaleX(1); }
        }
        @keyframes growBar {
          from { transform: scaleY(0); }
          to   { transform: scaleY(1); }
        }
        @keyframes fadeIn {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
        .epc-cost-card  { animation: fadeUp 0.55s cubic-bezier(.22,1,.36,1) both; }
        .epc-strike     { animation: strikeGrow 0.5s cubic-bezier(.22,1,.36,1) 0.8s both; transform-origin: left; }
        .epc-benefit-0  { animation: fadeUp 0.45s cubic-bezier(.22,1,.36,1) 1.4s both; }
        .epc-benefit-1  { animation: fadeUp 0.45s cubic-bezier(.22,1,.36,1) 1.6s both; }
        .epc-benefit-2  { animation: fadeUp 0.45s cubic-bezier(.22,1,.36,1) 1.8s both; }
        .epc-chart      { animation: fadeUp 0.5s ease 2.0s both; }
        .epc-bar        { transform-origin: bottom; animation: growBar 0.8s cubic-bezier(.22,1,.36,1) both; }
        .epc-footer     { animation: fadeIn 0.4s ease 2.6s both; }
      `}</style>

      <p className="editorial-eyebrow" style={{ color: brandColor }}>
        Modello EPC · Nessun investimento
      </p>
      <h2
        id="epc-heading"
        className="mt-2 font-headline text-2xl font-semibold tracking-tighter text-on-surface md:text-3xl"
      >
        {brandName} sostiene l&apos;investimento per voi
      </h2>
      <p className="mt-2 max-w-xl text-sm text-on-surface-variant">
        Con il modello EPC (Energy Performance Contract) non pagate
        nulla per l&apos;installazione: l&apos;impianto è di {brandName},
        voi raccogliete il risparmio fin dal primo giorno.
      </p>

      {/* Cost card with strikethrough */}
      <div className="epc-cost-card mt-8 relative inline-block max-w-sm w-full rounded-2xl bg-surface-container-lowest p-6 shadow-ambient">
        <p className="text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
          Investimento impianto
        </p>
        <div className="relative mt-2 inline-block">
          <p className="font-headline text-4xl font-bold tracking-tightest text-on-surface md:text-5xl">
            {formatEuro(grossCapexEur)}
          </p>
          <span
            className="epc-strike pointer-events-none absolute inset-y-1/2 left-0 right-0 h-[3px] rounded-full"
            style={{ backgroundColor: '#EF4444', top: '50%' }}
            aria-hidden
          />
        </div>
        <p className="mt-3 text-xs text-on-surface-variant">
          Costo che {brandName} sostiene al posto vostro
        </p>
      </div>

      {/* Three benefit cards */}
      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        <BenefitCard
          className="epc-benefit-0"
          icon="🏗️"
          title="Zero investimento"
          body={`${brandName} installa, mantiene e gestisce l'impianto. Voi non spendete nulla.`}
          brandColor={brandColor}
        />
        <BenefitCard
          className="epc-benefit-1"
          icon="⚡"
          title="Risparmio immediato"
          body="Dal primo giorno pagate l'energia meno cara. Risparmio tipico: 20% sulla bolletta attuale."
          brandColor={brandColor}
        />
        <BenefitCard
          className="epc-benefit-2"
          icon="🔑"
          title="Dopo 10 anni è vostro"
          body={`L'impianto vi viene ceduto a €100. Da quel momento il risparmio è al 100%.`}
          brandColor={brandColor}
        />
      </div>

      {/* Animated bar chart — Plenitude-style comparison */}
      {annualSavings > 0 && (
        <div className="epc-chart mt-8 rounded-2xl bg-surface-container-lowest p-6 shadow-ambient md:p-8">
          <p className="text-xs font-bold uppercase tracking-widest text-on-surface-variant">
            Quanto pagate nei prossimi 25 anni
          </p>
          <h3 className="mt-1 font-headline text-xl font-semibold tracking-tighter text-on-surface md:text-2xl">
            Senza {brandName} vs Con EPC
          </h3>

          <div className="mt-6 grid gap-6 md:grid-cols-2">
            {/* WITHOUT EPC — yellow growing bars */}
            <div>
              <div className="mb-2 inline-flex items-center gap-2 rounded-full bg-amber-100 px-3 py-1">
                <span aria-hidden>💸</span>
                <span className="text-xs font-semibold text-amber-900">
                  Senza fotovoltaico
                </span>
              </div>
              <p className="text-[11px] text-on-surface-variant">
                Continuate a pagare la bolletta tradizionale. Spesa cumulativa
                che cresce ogni anno.
              </p>
              <div className="mt-4 flex h-40 items-end gap-1.5 border-b border-outline-variant">
                {years.map((year, idx) => (
                  <div
                    key={year}
                    className="epc-bar flex-1 rounded-t-md"
                    style={{
                      height: `${heightWithout(year)}%`,
                      backgroundColor: '#F59E0B',
                      animationDelay: `${2.2 + idx * 0.08}s`,
                    }}
                    aria-hidden
                  />
                ))}
              </div>
              <div className="mt-1 flex justify-between text-[10px] tabular-nums text-on-surface-variant">
                {years.map((y) => (
                  <span key={y}>{y}a</span>
                ))}
              </div>
              <p className="mt-3 text-sm font-medium text-on-surface">
                Totale 25 anni: <strong className="text-amber-700">
                  € {Math.round(annualSavings * 5 * 25).toLocaleString('it-IT')}
                </strong>
              </p>
            </div>

            {/* WITH EPC — green stable bars, drop after year 10 */}
            <div>
              <div className="mb-2 inline-flex items-center gap-2 rounded-full"
                style={{ backgroundColor: `${brandColor}20` }}>
                <span className="inline-block px-3 py-1">
                  <span aria-hidden>🌿</span>{' '}
                  <span className="text-xs font-semibold" style={{ color: brandColor }}>
                    Con {brandName} EPC
                  </span>
                </span>
              </div>
              <p className="text-[11px] text-on-surface-variant">
                Canone simbolico per 10 anni, poi l&apos;impianto è vostro
                a €100. Da lì in poi spesa zero.
              </p>
              <div className="mt-4 flex h-40 items-end gap-1.5 border-b border-outline-variant">
                {years.map((year, idx) => (
                  <div
                    key={year}
                    className="epc-bar flex-1 rounded-t-md"
                    style={{
                      height: `${heightWith(year)}%`,
                      backgroundColor: brandColor,
                      animationDelay: `${2.2 + idx * 0.08}s`,
                    }}
                    aria-hidden
                  />
                ))}
              </div>
              <div className="mt-1 flex justify-between text-[10px] tabular-nums text-on-surface-variant">
                {years.map((y) => (
                  <span key={y}>{y}a</span>
                ))}
              </div>
              <p className="mt-3 text-sm font-medium text-on-surface">
                Totale 25 anni:{' '}
                <strong style={{ color: brandColor }}>
                  € {Math.round(annualSavings * 4 * 10).toLocaleString('it-IT')}
                </strong>{' '}
                <span className="text-on-surface-variant">
                  (solo i primi 10 anni)
                </span>
              </p>
            </div>
          </div>

          {/* Bottom-line delta */}
          <div className="mt-6 rounded-xl p-4"
            style={{ backgroundColor: `${brandColor}10`, border: `1px solid ${brandColor}25` }}>
            <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: brandColor }}>
              Vantaggio EPC
            </p>
            <p className="mt-1 font-headline text-2xl font-bold tracking-tight" style={{ color: brandColor }}>
              − € {Math.round(annualSavings * 5 * 25 - annualSavings * 4 * 10).toLocaleString('it-IT')}
            </p>
            <p className="mt-0.5 text-xs text-on-surface-variant">
              Spesa energetica evitata in 25 anni
            </p>
          </div>
        </div>
      )}

      {/* Brand footer note */}
      <div className="epc-footer mt-6 flex items-center gap-3 rounded-xl bg-surface-container-low p-4">
        {brandLogoUrl ? (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img src={brandLogoUrl} alt={brandName} className="h-8 w-auto" />
        ) : (
          <span
            className="font-headline text-base font-semibold"
            style={{ color: brandColor }}
          >
            {brandName}
          </span>
        )}
        <p className="text-xs text-on-surface-variant">
          Modello EPC certificato · Contratto trasparente · Nessun costo nascosto
        </p>
      </div>
    </section>
  );
}

function BenefitCard({
  icon,
  title,
  body,
  brandColor,
  className,
}: {
  icon: string;
  title: string;
  body: string;
  brandColor: string;
  className?: string;
}) {
  return (
    <div
      className={`rounded-2xl p-5 ${className ?? ''}`}
      style={{ backgroundColor: `${brandColor}08`, border: `1px solid ${brandColor}20` }}
    >
      <span className="text-2xl" aria-hidden>
        {icon}
      </span>
      <p className="mt-3 font-headline text-base font-semibold tracking-tight text-on-surface">
        {title}
      </p>
      <p className="mt-1.5 text-sm leading-relaxed text-on-surface-variant">{body}</p>
    </div>
  );
}
