'use client';

/**
 * SavingsComparePanel — predicted vs actual savings, side-by-side.
 *
 * Sprint 8 Fase B.4 + Sprint client-feedback (EPC + savings hero).
 * Renders the result of GET /v1/public/lead/{slug}/savings-compare.
 * Returns null until at least one bolletta has been uploaded — the
 * BillUploadCard sits above and triggers the re-fetch.
 *
 * Visual logic:
 *   * Prominent savings hero: "Con il solare paghi X€/anno invece di
 *     Y€/anno" — the number the lead reads first.
 *   * "Stima {brandName}" column (left)  — predicted_yearly_kwh + savings
 *   * Bolletta reale column (right) — actual_yearly_kwh + €/kWh tariff
 *   * Amber delta badge when actual_savings > predicted (tariff above
 *     market average → faster payback).
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
  brandName?: string;
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

export function SavingsComparePanel({ slug, refreshKey, brandColor, brandName = 'SolarLead' }: Props) {
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

  // "Con il solare paghi X€/anno invece di Y€/anno"
  const netYearlyCost = Math.max(0, data.actual_yearly_eur - data.actual_yearly_savings_eur);
  const savingsPct =
    data.actual_yearly_eur > 0
      ? Math.round(((data.actual_yearly_eur - netYearlyCost) / data.actual_yearly_eur) * 100)
      : 0;

  // Amber if actual beats prediction by ≥10% — the pitch is "your
  // tariff is above market average, the rooftop pays back faster".
  const beatsForecast = data.delta_pct >= 10;
  const deltaColor = beatsForecast ? '#F59E0B' : brandColor;

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
          Con il fotovoltaico, la tua spesa energetica scende a
        </p>
        <div className="mt-2 flex flex-wrap items-end gap-3">
          <span
            className="font-headline text-5xl font-bold tracking-tightest md:text-6xl"
            style={{ color: brandColor }}
          >
            {formatEuro(netYearlyCost)}
          </span>
          <span className="mb-1 text-base font-medium text-on-surface-variant">/anno</span>
        </div>
        <div className="mt-2 flex items-center gap-2 text-sm text-on-surface-variant">
          <span className="line-through opacity-60">
            invece di {formatEuro(data.actual_yearly_eur)}/anno
          </span>
          {savingsPct > 0 && (
            <span
              className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-bold text-white"
              style={{ backgroundColor: brandColor }}
            >
              −{savingsPct}%
            </span>
          )}
        </div>
        <p className="mt-3 text-xs text-on-surface-variant">
          Risparmio annuo stimato: <strong>{formatEuro(data.actual_yearly_savings_eur)}</strong> ·
          Rientro reale: <strong>{formatYears(data.actual_payback_years)}</strong>
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

      {/* ── Delta badge ───────────────────────────────────────── */}
      {beatsForecast && (
        <div
          className="mt-5 flex flex-wrap items-center gap-3 rounded-2xl px-5 py-4"
          style={{ backgroundColor: `${deltaColor}15` }}
        >
          <span
            className="inline-flex h-9 w-9 items-center justify-center rounded-full text-sm font-bold text-white"
            style={{ backgroundColor: deltaColor }}
            aria-hidden
          >
            +{Math.round(data.delta_pct)}%
          </span>
          <div className="flex-1">
            <p className="font-headline text-base font-semibold text-on-surface">
              La tua tariffa è sopra la media: il tetto rientra prima del previsto
            </p>
            <p className="text-sm text-on-surface-variant">
              Risparmio extra rispetto alla stima: {formatEuro(Math.abs(data.delta_savings_eur))}/anno
            </p>
          </div>
        </div>
      )}
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
