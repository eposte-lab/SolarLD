'use client';

/**
 * EpcPropositionSection — pitch commerciale del modello EPC.
 *
 * Redesign feedback Total Trade:
 *   - La motion NON parte più al mount: un IntersectionObserver attiva
 *     la sequenza quando la sezione entra nel viewport (la classe
 *     `.epc-playing` abilita le keyframe). Risolve il "sembra statico".
 *   - Timeline distesa ~10s, ritmo calmo.
 *   - Icone line-art personalizzate (`./icons/epc-icons`), zero emoji.
 *   - Confronto a due colonne: "Investimento diretto" (compri tu
 *     l'impianto: esborso iniziale → tempo di ritorno) vs "EPC Total
 *     Trade" (zero investimento, positivo dal giorno 1, a fine
 *     contratto l'impianto è tuo).
 *
 * Puro CSS keyframes + RAF. Nessun video, nessuna libreria di animazione.
 */

import React, { useEffect, useRef, useState } from 'react';

import {
  IconImmediateSaving,
  IconMinus,
  IconOwnership,
  IconPlus,
  IconZeroInvest,
} from './icons/epc-icons';

type Props = {
  grossCapexEur: number;
  brandName: string;
  brandColor: string;
  brandLogoUrl?: string | null;
  yearlySavingsEur?: number | null;
};

/** Anni di contratto EPC prima della cessione dell'impianto al cliente. */
const CONTRACT_YEARS = 10;
/** Quota del risparmio che il cliente trattiene durante il contratto
 *  EPC: ~20% di sconto sulla bolletta fino alla cessione dell'impianto.
 *  Stima tarabile. */
const EPC_CLIENT_SHARE = 0.2;

/** Colori delle barre della colonna "Investimento diretto", anno per
 *  anno [1,3,5,7,10,15,20,25]: primi anni in rosso (sei in perdita),
 *  poi arancione, infine sfuma a verde man mano che rientri. */
const DIRECT_BAR_COLORS = [
  '#DC2626',
  '#DC2626',
  '#E0822A',
  '#E0822A',
  '#A6A036',
  '#8C9433',
  '#74882F',
  '#5E7E2E',
];

/** Colori delle barre della colonna "EPC Total Trade": verde acceso —
 *  l'opzione vincente, sempre in positivo. Sfuma da un verde chiaro
 *  brillante a un verde pieno. */
const EPC_BAR_COLORS = [
  '#5BE08A',
  '#48D97D',
  '#36D070',
  '#27C763',
  '#1CBD57',
  '#19AC4F',
  '#179B47',
  '#158A40',
];

/** Trigger one-shot: `inView` diventa true quando l'elemento entra nel
 *  viewport, poi l'observer si disconnette. */
function useInViewOnce<T extends HTMLElement>(): [
  React.RefObject<T | null>,
  boolean,
] {
  const ref = useRef<T>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || inView) return;
    if (typeof IntersectionObserver === 'undefined') {
      setInView(true);
      return;
    }
    const obs = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            setInView(true);
            obs.disconnect();
          }
        }
      },
      { threshold: 0.2 },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [inView]);

  return [ref, inView];
}

/** Counter € animato. Parte solo quando `start` diventa true. */
function AnimatedEuroCounter({
  target,
  start,
  duration = 1800,
  delayMs = 0,
  className,
  style,
}: {
  target: number;
  start: boolean;
  duration?: number;
  delayMs?: number;
  className?: string;
  style?: React.CSSProperties;
}) {
  const [value, setValue] = useState(0);

  useEffect(() => {
    if (!start) return;
    const startAt = performance.now() + delayMs;
    let raf = 0;
    const step = (now: number) => {
      const elapsed = now - startAt;
      if (elapsed < 0) {
        raf = requestAnimationFrame(step);
        return;
      }
      const t = Math.min(1, elapsed / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(Math.round(target * eased));
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [start, target, duration, delayMs]);

  return (
    <span className={className} style={style}>
      € {value.toLocaleString('it-IT')}
    </span>
  );
}

type ChartPoint = { year: number; value: number };

/** Colonna grafico cash-flow con asse dello zero. Le barre positive
 *  crescono verso l'alto dalla linea dello zero, le negative verso il
 *  basso. Tutte le colonne condividono la stessa scala. */
function CashFlowChart({
  points,
  zeroTopPct,
  gMax,
  gMin,
  colorFor,
  baseDelay,
  heightClass = 'h-56',
}: {
  points: ChartPoint[];
  zeroTopPct: number;
  gMax: number;
  gMin: number;
  colorFor: (p: ChartPoint, idx: number) => string;
  baseDelay: number;
  heightClass?: string;
}) {
  return (
    <div>
      <div className={`relative ${heightClass}`}>
        {/* Linea dello zero */}
        <div
          className="absolute inset-x-0 border-t border-dashed border-on-surface/25"
          style={{ top: `${zeroTopPct}%` }}
          aria-hidden
        />
        <div className="absolute inset-0 flex items-stretch gap-1.5">
          {points.map((p, idx) => {
            const positive = p.value >= 0;
            const barPct = positive
              ? (p.value / (gMax || 1)) * zeroTopPct
              : (Math.abs(p.value) / (Math.abs(gMin) || 1)) * (100 - zeroTopPct);
            const barStyle: React.CSSProperties = {
              height: `${Math.max(barPct, 1)}%`,
              background: colorFor(p, idx),
              animationDelay: `${baseDelay + idx * 0.16}s`,
            };
            if (positive) barStyle.bottom = `${100 - zeroTopPct}%`;
            else barStyle.top = `${zeroTopPct}%`;
            return (
              <div key={p.year} className="relative flex-1" aria-hidden>
                <div
                  className={`epc-cfbar absolute inset-x-0 ${
                    positive
                      ? 'epc-cfbar-up rounded-t-sm'
                      : 'epc-cfbar-down rounded-b-sm'
                  }`}
                  style={barStyle}
                />
              </div>
            );
          })}
        </div>
      </div>
      <div className="mt-1.5 flex gap-1.5">
        {points.map((p) => (
          <span
            key={p.year}
            className="flex-1 text-center text-[10px] tabular-nums text-on-surface-variant"
          >
            {p.year}a
          </span>
        ))}
      </div>
    </div>
  );
}

function ProCon({
  positive,
  children,
  color,
}: {
  positive: boolean;
  children: React.ReactNode;
  color: string;
}) {
  return (
    <li className="flex items-start gap-2 text-[13px] leading-snug text-on-surface">
      <span className="mt-px shrink-0" style={{ color: positive ? color : '#C2410C' }}>
        {positive ? <IconPlus size={16} /> : <IconMinus size={16} />}
      </span>
      <span>{children}</span>
    </li>
  );
}

export function EpcPropositionSection({
  grossCapexEur,
  brandName,
  brandColor,
  brandLogoUrl,
  yearlySavingsEur,
}: Props) {
  const [ref, played] = useInViewOnce<HTMLElement>();

  const annualSavings = Math.max(0, yearlySavingsEur ?? 0);
  const years = [1, 3, 5, 7, 10, 15, 20, 25];

  // Cash-flow cumulato.
  const directNet = (y: number) => -grossCapexEur + annualSavings * y;
  const epcNet = (y: number) =>
    y <= CONTRACT_YEARS
      ? annualSavings * EPC_CLIENT_SHARE * y
      : annualSavings * EPC_CLIENT_SHARE * CONTRACT_YEARS +
        annualSavings * (y - CONTRACT_YEARS);

  const directPoints: ChartPoint[] = years.map((y) => ({ year: y, value: directNet(y) }));
  const epcPoints: ChartPoint[] = years.map((y) => ({ year: y, value: epcNet(y) }));

  // Scala condivisa fra le due colonne.
  const allVals = [...directPoints, ...epcPoints].map((p) => p.value);
  const gMax = Math.max(...allVals, 1);
  const gMin = Math.min(...allVals, 0);
  const range = gMax - gMin || 1;
  const zeroTopPct = (gMax / range) * 100;

  const paybackYears =
    annualSavings > 0 ? Math.max(1, Math.round(grossCapexEur / annualSavings)) : null;

  const showChart = annualSavings > 0;

  return (
    <section
      ref={ref}
      className={`mx-auto max-w-6xl px-6 py-8 ${played ? 'epc-playing' : ''}`}
      aria-labelledby="epc-heading"
    >
      <style>{`
        @keyframes epcFadeUp {
          from { opacity: 0; transform: translateY(16px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes epcScribble {
          from { stroke-dashoffset: 600; }
          to   { stroke-dashoffset: 0; }
        }
        @keyframes epcPop {
          0%   { opacity: 0; transform: scale(0.4); }
          65%  { opacity: 1; transform: scale(1.05); }
          100% { opacity: 1; transform: scale(1); }
        }
        @keyframes epcGrowUp   { from { transform: scaleY(0); } to { transform: scaleY(1); } }
        @keyframes epcGrowDown { from { transform: scaleY(0); } to { transform: scaleY(1); } }
        @keyframes epcFadeIn   { from { opacity: 0; } to { opacity: 1; } }

        /* Stato pre-play: tutto fermo e nascosto finché la sezione non
           entra nel viewport. */
        .epc-anim       { opacity: 0; }
        .epc-cfbar      { transform: scaleY(0); }
        .epc-cfbar-up   { transform-origin: bottom; }
        .epc-cfbar-down { transform-origin: top; }

        .epc-playing .epc-cost     { animation: epcFadeUp 0.7s cubic-bezier(.22,1,.36,1) 0.2s both; }
        .epc-playing .epc-scribble path { stroke-dasharray: 600; animation: epcScribble 0.9s cubic-bezier(.4,0,.2,1) 2.2s both; }
        .epc-playing .epc-zero     { animation: epcPop 0.8s cubic-bezier(.34,1.4,.5,1) 3.1s both; }
        .epc-playing .epc-b0       { animation: epcFadeUp 0.7s cubic-bezier(.22,1,.36,1) 4.0s both; }
        .epc-playing .epc-b1       { animation: epcFadeUp 0.7s cubic-bezier(.22,1,.36,1) 4.3s both; }
        .epc-playing .epc-b2       { animation: epcFadeUp 0.7s cubic-bezier(.22,1,.36,1) 4.6s both; }
        .epc-playing .epc-compare  { animation: epcFadeUp 0.7s cubic-bezier(.22,1,.36,1) 5.3s both; }
        .epc-playing .epc-cfbar-up   { animation: epcGrowUp 0.75s cubic-bezier(.22,1,.36,1) both; }
        .epc-playing .epc-cfbar-down { animation: epcGrowDown 0.75s cubic-bezier(.22,1,.36,1) both; }
        .epc-playing .epc-callout  { animation: epcFadeUp 0.7s cubic-bezier(.22,1,.36,1) 8.7s both; }
        .epc-playing .epc-footer   { animation: epcFadeIn 0.6s ease 9.4s both; }

        @media (prefers-reduced-motion: reduce) {
          .epc-anim, .epc-cfbar { opacity: 1 !important; transform: none !important; }
          .epc-playing *        { animation-duration: 0.01s !important; }
        }
      `}</style>

      <p className="editorial-eyebrow" style={{ color: brandColor }}>
        Modello EPC · Investi zero
      </p>
      <h2
        id="epc-heading"
        className="mt-2 font-headline text-2xl font-semibold tracking-tighter text-on-surface md:text-3xl"
      >
        {brandName} sostiene l&apos;investimento, voi raccogliete il risparmio
      </h2>
      <p className="mt-2 max-w-xl text-sm text-on-surface-variant">
        Con il contratto EPC non pagate nulla per l&apos;installazione:
        l&apos;impianto è di {brandName}, voi risparmiate fin dal primo
        giorno e a fine contratto l&apos;impianto diventa vostro.
      </p>

      {/* COSTO IMPIANTO → €0 */}
      <div className="mt-8 grid items-center gap-4 sm:grid-cols-[1fr_auto_1fr] sm:gap-6">
        <div className="epc-anim epc-cost relative rounded-2xl bg-surface-container-lowest p-6 shadow-ambient">
          <p className="text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
            Investimento impianto
          </p>
          <div className="relative mt-2 inline-block">
            <AnimatedEuroCounter
              target={grossCapexEur}
              start={played}
              duration={1900}
              delayMs={500}
              className="font-headline text-4xl font-bold tracking-tightest text-on-surface md:text-5xl"
            />
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
            Il costo che {brandName} sostiene al posto vostro
          </p>
        </div>

        <div
          aria-hidden
          className="mx-auto hidden sm:block"
          style={{ color: brandColor, opacity: 0.45 }}
        >
          <svg width="34" height="20" viewBox="0 0 34 20" fill="none">
            <path
              d="M2 10h28M22 3l9 7-9 7"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>

        <div
          className="epc-anim epc-zero rounded-2xl p-6 text-center shadow-ambient"
          style={{
            background: `linear-gradient(135deg, ${brandColor}22 0%, ${brandColor}06 100%)`,
            border: `2px solid ${brandColor}`,
          }}
        >
          <p
            className="text-xs font-semibold uppercase tracking-widest"
            style={{ color: brandColor }}
          >
            Quanto pagate voi
          </p>
          <p
            className="mt-2 font-headline text-5xl font-bold tracking-tightest md:text-6xl"
            style={{ color: brandColor }}
          >
            € 0
          </p>
          <p className="mt-2 text-xs font-medium" style={{ color: brandColor }}>
            Da subito, per tutta la durata del contratto
          </p>
        </div>
      </div>

      {/* BENEFIT */}
      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        <BenefitCard
          className="epc-anim epc-b0"
          icon={<IconZeroInvest size={26} />}
          title="Zero investimento"
          body={`${brandName} progetta, installa e gestisce l'impianto. Voi non immobilizzate capitale.`}
          brandColor={brandColor}
        />
        <BenefitCard
          className="epc-anim epc-b1"
          icon={<IconImmediateSaving size={26} />}
          title="Risparmio immediato"
          body="Dal primo giorno pagate l'energia meno cara: il rischio tecnico è a carico nostro."
          brandColor={brandColor}
        />
        <BenefitCard
          className="epc-anim epc-b2"
          icon={<IconOwnership size={26} />}
          title="A fine contratto è vostro"
          body={`Dopo ${CONTRACT_YEARS} anni l'impianto vi viene ceduto: da lì il 100% del risparmio resta a voi.`}
          brandColor={brandColor}
        />
      </div>

      {/* CONFRONTO — Investimento diretto vs EPC */}
      {showChart && (
        <div className="epc-anim epc-compare mt-8 rounded-2xl bg-surface-container-lowest p-6 shadow-ambient md:p-8">
          <p className="text-xs font-bold uppercase tracking-widest text-on-surface-variant">
            Due modi di mettere il fotovoltaico
          </p>
          <h3 className="mt-1 font-headline text-xl font-semibold tracking-tighter text-on-surface md:text-2xl">
            Investimento diretto vs EPC {brandName}
          </h3>
          <p className="mt-1 text-[11px] text-on-surface-variant">
            Posizione di cassa anno per anno. La linea tratteggiata è il
            punto di pareggio: con l&apos;EPC non ci finite mai sotto.
          </p>

          <div className="mt-6 grid items-start gap-5 md:grid-cols-[0.8fr_1.2fr]">
            {/* INVESTIMENTO DIRETTO — opzione secondaria, smorzata */}
            <div className="rounded-2xl bg-surface-container p-4 opacity-70 md:p-5">
              <div className="mb-2 inline-flex items-center rounded-full bg-surface-container-high px-3 py-1">
                <span className="text-xs font-semibold text-on-surface-variant">
                  Investimento diretto
                </span>
              </div>
              <ul className="mb-4 mt-1.5 space-y-1.5">
                <ProCon positive={false} color={brandColor}>
                  Esborso iniziale a vostro carico
                </ProCon>
                <ProCon positive={false} color={brandColor}>
                  Anni in rosso prima del rientro
                </ProCon>
                <ProCon positive={false} color={brandColor}>
                  Rischio tecnico e gestione su di voi
                </ProCon>
              </ul>
              <CashFlowChart
                points={directPoints}
                zeroTopPct={zeroTopPct}
                gMax={gMax}
                gMin={gMin}
                baseDelay={6.0}
                heightClass="h-40"
                colorFor={(_p, idx) => DIRECT_BAR_COLORS[idx] ?? '#5E7E2E'}
              />
              <p className="mt-2 text-[11px] text-on-surface-variant">
                Capitale immobilizzato e in rosso per
                {paybackYears ? ` ~${paybackYears} anni` : ' diversi anni'}.
              </p>
            </div>

            {/* EPC — opzione consigliata, in evidenza */}
            <div
              className="rounded-2xl p-4 ring-2 md:p-5"
              style={{
                backgroundColor: `${brandColor}0A`,
                boxShadow: `0 0 0 2px ${brandColor}`,
              }}
            >
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span
                  className="inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold"
                  style={{ backgroundColor: `${brandColor}20`, color: brandColor }}
                >
                  EPC {brandName}
                </span>
                <span
                  className="inline-flex items-center rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-white"
                  style={{ backgroundColor: brandColor }}
                >
                  Consigliato
                </span>
              </div>
              <ul className="mb-4 mt-1.5 space-y-1.5">
                <ProCon positive color={brandColor}>
                  Zero investimento — rischio tecnico tutto su di noi
                </ProCon>
                <ProCon positive color={brandColor}>
                  Già durante il contratto risparmiate circa il{' '}
                  <strong>20% sulla bolletta</strong>
                </ProCon>
                <ProCon positive color={brandColor}>
                  Dopo {CONTRACT_YEARS} anni l&apos;impianto è ceduto a voi:
                  da lì il risparmio è pieno
                </ProCon>
              </ul>
              <CashFlowChart
                points={epcPoints}
                zeroTopPct={zeroTopPct}
                gMax={gMax}
                gMin={gMin}
                baseDelay={6.9}
                heightClass="h-56"
                colorFor={(_p, idx) => EPC_BAR_COLORS[idx] ?? '#158A40'}
              />
              <p className="mt-2 text-[11px] text-on-surface-variant">
                Sempre in positivo: ~20% di sconto in bolletta per{' '}
                {CONTRACT_YEARS} anni, poi l&apos;impianto è vostro e il
                risparmio diventa pieno.
              </p>
            </div>
          </div>

          {/* CALLOUT */}
          <div
            className="epc-anim epc-callout mt-6 rounded-xl p-4"
            style={{
              backgroundColor: `${brandColor}10`,
              border: `1px solid ${brandColor}25`,
            }}
          >
            <p
              className="text-xs font-semibold uppercase tracking-widest"
              style={{ color: brandColor }}
            >
              Capitale che non immobilizzate con l&apos;EPC
            </p>
            <p
              className="mt-1 font-headline text-3xl font-bold tracking-tight md:text-4xl"
              style={{ color: brandColor }}
            >
              <AnimatedEuroCounter
                target={grossCapexEur}
                start={played}
                duration={1500}
                delayMs={8900}
                style={{ color: brandColor }}
              />
            </p>
            <p className="mt-0.5 text-xs text-on-surface-variant">
              {paybackYears
                ? `Con l'investimento diretto rientrereste dopo ~${paybackYears} anni di capitale in rosso. Con l'EPC ${brandName} non immobilizzate nulla: già durante il contratto risparmiate circa il 20% sulla bolletta e dopo ${CONTRACT_YEARS} anni l'impianto è ceduto a voi.`
                : `Con l'EPC ${brandName} non immobilizzate capitale: ~20% di risparmio in bolletta durante il contratto e dopo ${CONTRACT_YEARS} anni l'impianto è vostro.`}
            </p>
          </div>
        </div>
      )}

      {/* FOOTER */}
      <div className="epc-anim epc-footer mt-6 flex items-center gap-3 rounded-xl bg-surface-container-low p-4">
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
          Modello EPC · Contratto trasparente · Nessun costo nascosto
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
  icon: React.ReactNode;
  title: string;
  body: string;
  brandColor: string;
  className?: string;
}) {
  return (
    <div
      className={`rounded-2xl p-5 ${className ?? ''}`}
      style={{
        backgroundColor: `${brandColor}08`,
        border: `1px solid ${brandColor}20`,
      }}
    >
      <span
        className="inline-flex h-11 w-11 items-center justify-center rounded-xl"
        style={{ backgroundColor: `${brandColor}14`, color: brandColor }}
      >
        {icon}
      </span>
      <p className="mt-3 font-headline text-base font-semibold tracking-tight text-on-surface">
        {title}
      </p>
      <p className="mt-1.5 text-sm leading-relaxed text-on-surface-variant">
        {body}
      </p>
    </div>
  );
}
