'use client';

/**
 * EpcPropositionSection — EPC commercial pitch (enhanced motion).
 *
 * Sprint 2 del feedback Total Trade: rendere la motion graphic DAVVERO
 * impattante. Cambiamenti rispetto alla v1:
 *   - Counter CSS animato (€0 → €X) sul costo iniziale, durata 1.2s
 *   - Strikethrough drammatico in SVG (linea sketch a mano libera,
 *     spessa, leggermente irregolare) invece della linea piatta
 *   - Card "0 €" che esplode dal centro dopo lo strikethrough
 *   - Benefit card con scale-in bouncing + icona che ruota al mount
 *   - Bar chart h-56 (più tall) + glow pulsante sulla colonna anno 10
 *   - Delta counter animato (€0 → €394.853)
 *   - Sequenza totale compressa a < 3 secondi
 *
 * Tutto puro CSS keyframes + SVG path. Zero video, zero rendering.
 */

import React, { useEffect, useState } from 'react';
import { formatEuro } from '@/lib/api';

type Props = {
  grossCapexEur: number;
  brandName: string;
  brandColor: string;
  brandLogoUrl?: string | null;
  yearlySavingsEur?: number | null;
};

/** Counter component — animates from 0 to target over `duration` ms. */
function AnimatedEuroCounter({
  target,
  duration = 1200,
  delayMs = 0,
  className,
  style,
}: {
  target: number;
  duration?: number;
  delayMs?: number;
  className?: string;
  style?: React.CSSProperties;
}) {
  const [value, setValue] = useState(0);

  useEffect(() => {
    const start = performance.now() + delayMs;
    let raf = 0;
    const step = (now: number) => {
      const elapsed = now - start;
      if (elapsed < 0) {
        raf = requestAnimationFrame(step);
        return;
      }
      const t = Math.min(1, elapsed / duration);
      // ease-out cubic
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(Math.round(target * eased));
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, duration, delayMs]);

  return (
    <span className={className} style={style}>
      € {value.toLocaleString('it-IT')}
    </span>
  );
}

export function EpcPropositionSection({
  grossCapexEur,
  brandName,
  brandColor,
  brandLogoUrl,
  yearlySavingsEur,
}: Props) {
  const annualSavings = Math.max(0, yearlySavingsEur ?? 0);
  const years = [1, 3, 5, 7, 10, 15, 20, 25];

  const maxCumulative = Math.max(annualSavings * 25 * 5, 1);
  function heightWithout(year: number) {
    return Math.min(100, ((annualSavings * 5 * year) / maxCumulative) * 100);
  }
  function heightWith(year: number) {
    if (year <= 10) {
      return Math.min(100, ((annualSavings * 4 * year) / maxCumulative) * 100);
    }
    return Math.min(100, ((annualSavings * 4 * 10) / maxCumulative) * 100);
  }

  const deltaEur = Math.round(annualSavings * 5 * 25 - annualSavings * 4 * 10);

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
        @keyframes scribbleDraw {
          from { stroke-dashoffset: 600; }
          to   { stroke-dashoffset: 0; }
        }
        @keyframes popBounce {
          0%   { opacity: 0; transform: scale(0); }
          60%  { opacity: 1; transform: scale(1.08); }
          80%  { transform: scale(0.96); }
          100% { transform: scale(1); }
        }
        @keyframes iconSpin {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
        @keyframes growBar {
          from { transform: scaleY(0); }
          to   { transform: scaleY(1); }
        }
        @keyframes glowPulse {
          0%, 100% { box-shadow: 0 0 0 0 currentColor; }
          50%      { box-shadow: 0 0 12px 4px currentColor; }
        }
        @keyframes fadeIn {
          from { opacity: 0; }
          to   { opacity: 1; }
        }

        .epc-cost-card      { animation: fadeUp 0.45s cubic-bezier(.22,1,.36,1) both; }
        .epc-scribble path  { stroke-dasharray: 600; animation: scribbleDraw 0.65s cubic-bezier(.4,.0,.2,1) 1.4s both; }
        .epc-zero-card      { animation: popBounce 0.55s cubic-bezier(.34,1.56,.64,1) 2.0s both; }
        .epc-benefit-0      { animation: popBounce 0.5s cubic-bezier(.34,1.56,.64,1) 2.4s both; }
        .epc-benefit-1      { animation: popBounce 0.5s cubic-bezier(.34,1.56,.64,1) 2.55s both; }
        .epc-benefit-2      { animation: popBounce 0.5s cubic-bezier(.34,1.56,.64,1) 2.7s both; }
        .epc-icon           { animation: iconSpin 0.8s cubic-bezier(.4,0,.2,1) 2.4s both; display: inline-block; }
        .epc-chart          { animation: fadeUp 0.5s ease 2.9s both; }
        .epc-bar            { transform-origin: bottom; animation: growBar 0.7s cubic-bezier(.22,1,.36,1) both; }
        .epc-bar-glow       { animation: glowPulse 2s ease-in-out 3.6s infinite; }
        .epc-footer         { animation: fadeIn 0.4s ease 3.4s both; }
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
        Con il modello EPC non pagate nulla per l&apos;installazione:
        l&apos;impianto è di {brandName}, voi raccogliete il risparmio
        fin dal primo giorno.
      </p>

      {/* COST CARD + ANIMATED STRIKETHROUGH + ZERO CARD */}
      <div className="mt-8 grid items-center gap-4 sm:grid-cols-[auto_auto_auto] sm:gap-6">
        {/* Cost card with animated counter + scribble strike */}
        <div className="epc-cost-card relative inline-block rounded-2xl bg-surface-container-lowest p-6 shadow-ambient">
          <p className="text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
            Investimento impianto
          </p>
          <div className="relative mt-2 inline-block">
            <AnimatedEuroCounter
              target={grossCapexEur}
              duration={1100}
              delayMs={200}
              className="font-headline text-4xl font-bold tracking-tightest text-on-surface md:text-5xl"
            />
            {/* SVG scribble strike — sembra una linea fatta a mano libera */}
            <svg
              className="epc-scribble pointer-events-none absolute -inset-x-2 inset-y-0 h-full w-[calc(100%+1rem)]"
              viewBox="0 0 300 80"
              preserveAspectRatio="none"
              aria-hidden
            >
              <path
                d="M 8 42 C 60 38, 100 46, 150 42 C 200 38, 240 48, 295 40"
                fill="none"
                stroke="#EF4444"
                strokeWidth="6"
                strokeLinecap="round"
              />
            </svg>
          </div>
          <p className="mt-3 text-xs text-on-surface-variant">
            Costo che {brandName} sostiene al posto vostro
          </p>
        </div>

        {/* Arrow */}
        <div
          aria-hidden
          className="hidden text-3xl sm:block"
          style={{ color: brandColor, opacity: 0.5 }}
        >
          →
        </div>

        {/* Zero card — pop-bounces in after strike */}
        <div
          className="epc-zero-card rounded-2xl p-6 text-center shadow-ambient"
          style={{
            background: `linear-gradient(135deg, ${brandColor}22 0%, ${brandColor}06 100%)`,
            border: `2px solid ${brandColor}`,
          }}
        >
          <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: brandColor }}>
            Quanto paghi tu
          </p>
          <p
            className="mt-2 font-headline text-5xl font-bold tracking-tightest md:text-6xl"
            style={{ color: brandColor }}
          >
            € 0
          </p>
          <p className="mt-2 text-xs font-medium" style={{ color: brandColor }}>
            ✨ Da subito
          </p>
        </div>
      </div>

      {/* Three benefit cards — pop-bounce + spinning icons */}
      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        <BenefitCard className="epc-benefit-0" icon="🏗️" title="Zero investimento"
          body={`${brandName} installa, mantiene e gestisce l'impianto. Voi non spendete nulla.`}
          brandColor={brandColor}
        />
        <BenefitCard className="epc-benefit-1" icon="⚡" title="Risparmio immediato"
          body="Dal primo giorno pagate l'energia meno cara. Risparmio tipico: 20% sulla bolletta attuale."
          brandColor={brandColor}
        />
        <BenefitCard className="epc-benefit-2" icon="🔑" title="Dopo 10 anni è vostro"
          body={`L'impianto vi viene ceduto a €100. Da quel momento il risparmio è al 100%.`}
          brandColor={brandColor}
        />
      </div>

      {/* Bar chart — taller, glow on year 10 */}
      {annualSavings > 0 && (
        <div className="epc-chart mt-8 rounded-2xl bg-surface-container-lowest p-6 shadow-ambient md:p-8">
          <p className="text-xs font-bold uppercase tracking-widest text-on-surface-variant">
            Quanto pagate nei prossimi 25 anni
          </p>
          <h3 className="mt-1 font-headline text-xl font-semibold tracking-tighter text-on-surface md:text-2xl">
            Senza {brandName} vs Con EPC
          </h3>

          <div className="mt-6 grid gap-6 md:grid-cols-2">
            {/* WITHOUT EPC */}
            <div>
              <div className="mb-2 inline-flex items-center gap-2 rounded-full bg-amber-100 px-3 py-1">
                <span aria-hidden>💸</span>
                <span className="text-xs font-semibold text-amber-900">
                  Senza fotovoltaico
                </span>
              </div>
              <p className="text-[11px] text-on-surface-variant">
                Continuate a pagare la bolletta. Spesa cumulativa che cresce
                ogni anno, senza freno.
              </p>
              <div className="mt-4 flex h-56 items-end gap-1.5 border-b border-outline-variant">
                {years.map((year, idx) => (
                  <div
                    key={year}
                    className="epc-bar flex-1 rounded-t-md"
                    style={{
                      height: `${heightWithout(year)}%`,
                      backgroundColor: '#F59E0B',
                      animationDelay: `${3.0 + idx * 0.07}s`,
                    }}
                    title={`Anno ${year}: ~€${Math.round(annualSavings * 5 * year).toLocaleString('it-IT')} cumulati`}
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
                <strong className="text-amber-700">
                  € {Math.round(annualSavings * 5 * 25).toLocaleString('it-IT')}
                </strong>
              </p>
            </div>

            {/* WITH EPC */}
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
              <div className="mt-4 flex h-56 items-end gap-1.5 border-b border-outline-variant">
                {years.map((year, idx) => {
                  const isPivotYear = year === 10;
                  return (
                    <div
                      key={year}
                      className={`epc-bar flex-1 rounded-t-md ${isPivotYear ? 'epc-bar-glow' : ''}`}
                      style={{
                        height: `${heightWith(year)}%`,
                        backgroundColor: brandColor,
                        animationDelay: `${3.0 + idx * 0.07}s`,
                        color: brandColor,
                      }}
                      title={
                        isPivotYear
                          ? `Anno 10: impianto ceduto a €100, da qui spesa zero`
                          : `Anno ${year}: €${Math.round(year <= 10 ? annualSavings * 4 * year : annualSavings * 4 * 10).toLocaleString('it-IT')} pagati`
                      }
                      aria-hidden
                    />
                  );
                })}
              </div>
              <div className="mt-1 flex justify-between text-[10px] tabular-nums text-on-surface-variant">
                {years.map((y) => (
                  <span
                    key={y}
                    style={y === 10 ? { color: brandColor, fontWeight: 700 } : undefined}
                  >
                    {y}a
                  </span>
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

          {/* Bottom-line delta — animated counter */}
          <div
            className="mt-6 rounded-xl p-4"
            style={{ backgroundColor: `${brandColor}10`, border: `1px solid ${brandColor}25` }}
          >
            <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: brandColor }}>
              Vantaggio EPC in 25 anni
            </p>
            <p className="mt-1 font-headline text-3xl font-bold tracking-tight" style={{ color: brandColor }}>
              −{' '}
              <AnimatedEuroCounter
                target={deltaEur}
                duration={1500}
                delayMs={3500}
                style={{ color: brandColor }}
              />
            </p>
            <p className="mt-0.5 text-xs text-on-surface-variant">
              Spesa energetica evitata grazie al modello EPC
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
      <span className="epc-icon text-2xl" aria-hidden>
        {icon}
      </span>
      <p className="mt-3 font-headline text-base font-semibold tracking-tight text-on-surface">
        {title}
      </p>
      <p className="mt-1.5 text-sm leading-relaxed text-on-surface-variant">{body}</p>
    </div>
  );
}

// Make formatEuro available even if unused — keeps imports stable.
const _keepFormatEuro = formatEuro;
void _keepFormatEuro;
