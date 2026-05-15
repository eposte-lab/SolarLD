'use client';

/**
 * SavingsComparePanel — risparmio calcolato sulla bolletta caricata.
 *
 * Renders the result of GET /v1/public/lead/{slug}/savings-compare.
 * Returns null until at least one bolletta has been uploaded — the
 * BillUploadCard sits above and triggers the re-fetch.
 *
 * Due modalità:
 *   * `epc` → modello EPC: "Oggi paghi €X" (dalla bolletta) → "con
 *     l'EPC €Y". Il risparmio è il 20% del valore dell'energia
 *     prodotta dall'impianto stimato nel dossier (non una % della
 *     bolletta). Niente payback (l'EPC è a investimento zero).
 *   * altrimenti → confronto stima vs bolletta reale (modello classico
 *     "compri tu l'impianto").
 *
 * Il parent passa un ``refreshKey`` che cambia a ogni upload, forzando
 * il re-fetch del pannello.
 */

import { useEffect, useState } from 'react';

import { API_URL, formatEuro, formatYears } from '@/lib/api';

type Props = {
  slug: string;
  refreshKey: number;
  brandColor: string;
  brandName?: string;
  epc?: boolean;
};

/** Quota dell'energia PRODOTTA dall'impianto che diventa risparmio del
 *  cliente durante il contratto EPC. Il 20% si calcola sul valore di
 *  quanto produce l'impianto (stima del dossier), non sulla bolletta:
 *  se l'impianto produce €1.000/mese di energia, €200/mese di risparmio. */
const EPC_CLIENT_SHARE = 0.2;
/** Durata del contratto EPC prima della cessione dell'impianto. */
const EPC_CONTRACT_YEARS = 10;

type CompareData = {
  available: true;
  uploaded_at: string;
  source: string;
  predicted_yearly_kwh: number;
  predicted_yearly_savings_eur: number;
  predicted_payback_years: number | null;
  actual_yearly_kwh: number;
  actual_yearly_eur: number;
  actual_tariff_eur_per_kwh: number;
  actual_yearly_savings_eur: number;
  actual_payback_years: number | null;
  actual_self_consumption_kwh: number;
  actual_export_kwh: number;
  delta_savings_eur: number;
  delta_pct: number;
};

type CompareResponse = CompareData | { available: false; reason: string };

export function SavingsComparePanel({
  slug,
  refreshKey,
  brandColor,
  brandName = 'SolarLead',
  epc = false,
}: Props) {
  const [data, setData] = useState<CompareData | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const res = await fetch(
          `${API_URL}/v1/public/lead/${encodeURIComponent(slug)}/savings-compare`,
          { cache: 'no-store' },
        );
        if (!res.ok) {
          if (!cancelled) setData(null);
          return;
        }
        const json = (await res.json()) as CompareResponse;
        if (cancelled) return;
        setData(json.available ? json : null);
      } catch {
        if (!cancelled) setData(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [slug, refreshKey]);

  if (!data) {
    if (loading) {
      return (
        <section className="bento-glass p-6 text-sm text-on-surface-variant">
          Calcolo del risparmio in corso…
        </section>
      );
    }
    return null;
  }

  // ── Modello EPC ─────────────────────────────────────────────────
  if (epc) {
    const bill = Math.max(0, data.actual_yearly_eur);
    // Il risparmio EPC è il 20% del VALORE IN DENARO dell'energia che
    // l'impianto produce: kWh prodotti × tariffa reale del cliente
    // (dalla bolletta caricata). Non è una % della bolletta.
    const plantValue = Math.max(
      0,
      data.predicted_yearly_kwh * data.actual_tariff_eur_per_kwh,
    );
    const epcSaving = plantValue * EPC_CLIENT_SHARE;
    const epcBill = Math.max(0, bill - epcSaving);
    const saving10y = epcSaving * EPC_CONTRACT_YEARS;
    const pctOff = bill > 0 ? Math.round((epcSaving / bill) * 100) : 0;
    // Il confronto "oggi paghi / con l'EPC" si mostra a importo mensile.
    const billMonthly = bill / 12;
    const epcBillMonthly = epcBill / 12;

    return (
      <section
        className="bento-glass p-6 md:p-8"
        aria-labelledby="savings-compare-heading"
      >
        <p className="editorial-eyebrow">La tua bolletta con l&apos;EPC</p>
        <h2
          id="savings-compare-heading"
          className="mt-2 font-headline text-2xl font-semibold tracking-tighter text-on-surface md:text-3xl"
        >
          Quanto risparmi con l&apos;EPC {brandName}
        </h2>
        <p className="mt-2 text-sm text-on-surface-variant">
          La bolletta caricata è il punto di partenza; il risparmio è il
          20% dell&apos;energia prodotta dall&apos;impianto stimato.
        </p>

        {/* Oggi → con l'EPC */}
        <div className="mt-6 grid gap-4 sm:grid-cols-[1fr_auto_1fr] sm:items-center">
          <div className="rounded-2xl bg-surface-container p-5">
            <p className="text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
              Oggi paghi
            </p>
            <p className="mt-2 font-headline text-3xl font-bold tracking-tightest text-on-surface md:text-4xl">
              {formatEuro(billMonthly)}
              <span className="ml-1 text-sm font-medium text-on-surface-variant">
                /mese
              </span>
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
            className="rounded-2xl p-5"
            style={{
              backgroundColor: `${brandColor}10`,
              border: `1.5px solid ${brandColor}30`,
            }}
          >
            <div className="flex flex-wrap items-center gap-2">
              <p
                className="text-xs font-semibold uppercase tracking-widest"
                style={{ color: brandColor }}
              >
                Con l&apos;EPC paghi
              </p>
              {pctOff > 0 ? (
                <span
                  className="rounded-full px-2 py-0.5 text-[10px] font-bold text-white"
                  style={{ backgroundColor: brandColor }}
                >
                  −{pctOff}%
                </span>
              ) : null}
            </div>
            <p
              className="mt-2 font-headline text-3xl font-bold tracking-tightest md:text-4xl"
              style={{ color: brandColor }}
            >
              {formatEuro(epcBillMonthly)}
              <span className="ml-1 text-sm font-medium text-on-surface-variant">
                /mese
              </span>
            </p>
          </div>
        </div>

        {/* Risparmio + cessione */}
        <div
          className="mt-4 rounded-2xl p-5"
          style={{
            backgroundColor: `${brandColor}0A`,
            border: `1px solid ${brandColor}20`,
          }}
        >
          <p className="text-sm text-on-surface">
            Risparmi{' '}
            <strong style={{ color: brandColor }}>
              {formatEuro(epcSaving)}/anno
            </strong>{' '}
            in bolletta — il 20% dell&apos;energia prodotta
            dall&apos;impianto, con <strong>zero investimento</strong>. In{' '}
            {EPC_CONTRACT_YEARS} anni di contratto sono{' '}
            <strong style={{ color: brandColor }}>
              {formatEuro(saving10y)}
            </strong>
            .
          </p>
          <p className="mt-2 text-xs text-on-surface-variant">
            Dopo {EPC_CONTRACT_YEARS} anni l&apos;impianto viene ceduto alla
            tua azienda: da lì il risparmio diventa pieno.
          </p>
        </div>
      </section>
    );
  }

  // ── Modello classico: stima vs bolletta reale ───────────────────
  return (
    <section
      className="bento-glass p-6 md:p-8"
      aria-labelledby="savings-compare-heading"
    >
      <p className="editorial-eyebrow">Confronto risparmio</p>
      <h2
        id="savings-compare-heading"
        className="mt-2 font-headline text-2xl font-semibold tracking-tighter text-on-surface md:text-3xl"
      >
        Stima {brandName} vs la tua bolletta reale
      </h2>

      {/* ── Prominent savings hero ─────────────────────────────── */}
      <div
        className="mt-6 rounded-2xl p-5 md:p-7"
        style={{ backgroundColor: `${brandColor}10`, border: `1.5px solid ${brandColor}30` }}
      >
        <p className="text-sm font-medium text-on-surface-variant">
          Con il fotovoltaico risparmi
        </p>
        <div className="mt-2 flex flex-wrap items-end gap-3">
          <span
            className="font-headline text-5xl font-bold tracking-tightest md:text-6xl"
            style={{ color: brandColor }}
          >
            {formatEuro(data.actual_yearly_savings_eur)}
          </span>
          <span className="mb-1 text-base font-medium text-on-surface-variant">/anno</span>
        </div>
        <p className="mt-3 text-xs text-on-surface-variant">
          Sulla tua bolletta attuale di{' '}
          <strong>{formatEuro(data.actual_yearly_eur)}/anno</strong> · Rientro
          reale: <strong>{formatYears(data.actual_payback_years)}</strong>
        </p>
      </div>

      {/* ── Side-by-side detail ────────────────────────────────── */}
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <div className="bento p-5">
          <p className="editorial-eyebrow">Stima {brandName}</p>
          <p className="mt-3 font-headline text-3xl font-semibold tracking-tightest text-on-surface md:text-4xl">
            {formatEuro(data.predicted_yearly_savings_eur)}
            <span className="ml-1 text-sm font-medium text-on-surface-variant">
              /anno
            </span>
          </p>
          <dl className="mt-4 space-y-1.5 text-sm text-on-surface-variant">
            <Row
              label="Energia prodotta dal pannello"
              value={`${Math.round(
                data.predicted_yearly_kwh,
              ).toLocaleString('it-IT')} kWh`}
            />
            <Row
              label="Rientro stimato"
              value={formatYears(data.predicted_payback_years)}
            />
          </dl>
        </div>

        <div className="bento p-5">
          <p className="editorial-eyebrow">La tua bolletta reale</p>
          <p className="mt-3 font-headline text-3xl font-semibold tracking-tightest text-on-surface md:text-4xl">
            {formatEuro(data.actual_yearly_savings_eur)}
            <span className="ml-1 text-sm font-medium text-on-surface-variant">
              /anno
            </span>
          </p>
          <dl className="mt-4 space-y-1.5 text-sm text-on-surface-variant">
            <Row
              label="Consumo annuo"
              value={`${Math.round(data.actual_yearly_kwh).toLocaleString(
                'it-IT',
              )} kWh`}
            />
            <Row
              label="Tariffa attuale"
              value={`${data.actual_tariff_eur_per_kwh.toLocaleString(
                'it-IT',
                { minimumFractionDigits: 2, maximumFractionDigits: 3 },
              )} €/kWh`}
            />
            <Row
              label="Rientro reale"
              value={formatYears(data.actual_payback_years)}
            />
          </dl>
        </div>
      </div>
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt>{label}</dt>
      <dd className="font-medium text-on-surface">{value}</dd>
    </div>
  );
}
