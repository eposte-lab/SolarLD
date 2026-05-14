'use client';

/**
 * EpcPropositionSection — animated EPC commercial proposition.
 *
 * Renders only when `tenant.epc_enabled = true`. Shows the Total
 * Trade / ESCO model: the tenant (not the lead) bears the full
 * installation cost, the lead saves ~20% on electricity immediately,
 * and after 10 years the plant is ceded at €100.
 *
 * Animation sequence (CSS keyframes, no JS timer):
 *   0.0s — large cost card fades in (fade-up)
 *   0.8s — red strikethrough line sweeps across the cost number
 *   1.4s — three benefit cards appear in stagger (200ms each)
 *   2.2s — footer note fades in
 *
 * Props:
 *   grossCapexEur  — gross plant cost before incentives (from roi_data)
 *   brandName      — tenant business_name (replaces "SolarLead")
 *   brandColor     — primary brand hex colour
 *   brandLogoUrl   — optional logo (falls back to text)
 */

import React from 'react';
import { formatEuro } from '@/lib/api';

type Props = {
  grossCapexEur: number;
  brandName: string;
  brandColor: string;
  brandLogoUrl?: string | null;
};

export function EpcPropositionSection({
  grossCapexEur,
  brandName,
  brandColor,
  brandLogoUrl,
}: Props) {
  return (
    <section
      className="mx-auto max-w-6xl px-6 py-8"
      aria-labelledby="epc-heading"
    >
      {/* Keyframe styles — scoped, no global pollution */}
      <style>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(14px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes strikeGrow {
          from { transform: scaleX(0); }
          to   { transform: scaleX(1); }
        }
        @keyframes fadeIn {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
        .epc-cost-card  { animation: fadeUp  0.55s cubic-bezier(.22,1,.36,1) both; }
        .epc-strike     { animation: strikeGrow 0.5s cubic-bezier(.22,1,.36,1) 0.8s both; transform-origin: left; }
        .epc-benefit-0  { animation: fadeUp  0.45s cubic-bezier(.22,1,.36,1) 1.4s both; }
        .epc-benefit-1  { animation: fadeUp  0.45s cubic-bezier(.22,1,.36,1) 1.6s both; }
        .epc-benefit-2  { animation: fadeUp  0.45s cubic-bezier(.22,1,.36,1) 1.8s both; }
        .epc-footer     { animation: fadeIn  0.4s ease 2.25s both; }
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
          {/* Red strikethrough */}
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
