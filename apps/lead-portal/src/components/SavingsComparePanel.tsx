'use client';

/**
 * SavingsComparePanel — predicted vs actual savings, side-by-side.
 *
 * Sprint 8 Fase B.4. Renders the result of
 * GET /v1/public/lead/{slug}/savings-compare. The panel hides itself
 * (returns null) until at least one bolletta has been uploaded —
 * the BillUploadCard sits above and triggers the re-fetch.
 *
 * Visual logic:
 *   * Stima SolarLD column (left)  — predicted_yearly_kwh + savings
 *   * Bolletta reale column (right) — actual_yearly_kwh + €/kWh tariff
 *   * Delta highlight: amber when actual_savings > predicted (the
 *     pitch is "you're paying above-average tariff, the rooftop
 *     pays back faster than the standard estimate"); mint when in
 *     line with prediction.
 *
 * The parent page passes a ``refreshKey`` that bumps each time
 * BillUploadCard.onSaved fires, forcing this panel to re-fetch.
 */

import { useEffect, useState } from 'react';

import { API_URL, formatEuro, formatYears } from '@/lib/api';

type Props = {
  slug: string;
  refreshKey: number;
  brandColor: string;
};

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

export function SavingsComparePanel({ slug, refreshKey, brandColor }: Props) {
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

  // Amber if actual beats prediction by ≥10% — the pitch is "your
  // tariff is above market average, the rooftop pays back faster".
  const beatsForecast = data.delta_pct >= 10;
  const deltaColor = beatsForecast ? '#F59E0B' : brandColor;
  const deltaLabel = beatsForecast
    ? 'Stai pagando più della media: il tetto rende prima'
    : 'In linea con la nostra stima';

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
        Stima SolarLD vs la tua bolletta reale
      </h2>

      <div className="mt-6 grid gap-4 md:grid-cols-2">
        <div className="bento p-5">
          <p className="editorial-eyebrow">Stima SolarLD</p>
          <p className="mt-3 font-headline text-3xl font-semibold tracking-tightest text-on-surface md:text-4xl">
            {formatEuro(data.predicted_yearly_savings_eur)}
            <span className="ml-1 text-sm font-medium text-on-surface-variant">
              /anno
            </span>
          </p>
          <dl className="mt-4 space-y-1.5 text-sm text-on-surface-variant">
            <Row
              label="Produzione attesa"
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

      <div
        className="mt-5 flex flex-wrap items-center gap-3 rounded-2xl px-5 py-4"
        style={{ backgroundColor: `${deltaColor}15` }}
      >
        <span
          className="inline-flex h-9 w-9 items-center justify-center rounded-full text-sm font-bold text-white"
          style={{ backgroundColor: deltaColor }}
          aria-hidden
        >
          {data.delta_pct >= 0 ? '+' : ''}
          {Math.round(data.delta_pct)}%
        </span>
        <div className="flex-1">
          <p className="font-headline text-base font-semibold text-on-surface">
            {deltaLabel}
          </p>
          <p className="text-sm text-on-surface-variant">
            Differenza annuale: {formatEuro(Math.abs(data.delta_savings_eur))}{' '}
            {data.delta_savings_eur >= 0 ? 'in più' : 'in meno'} rispetto alla
            stima media.
          </p>
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
