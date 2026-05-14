'use client';

/**
 * BehindTheNumbersPanel — la matematica del risparmio, esposta.
 *
 * Sprint 1 del feedback Total Trade: il lead vede "Risparmio €X/anno"
 * ma non capisce DA DOVE viene quel numero. Questa card expandable
 * (default collapsed) mostra il calcolo passo-passo:
 *
 *   1. Produzione del tetto (Google Solar API)
 *   2. Consumo stimato del settore × superficie del tetto
 *   3. Autoconsumo = min(produzione, consumo) × tariffa di rete
 *   4. Energia ceduta = max(produzione − consumo, 0) × RID (€0.09/kWh)
 *   5. Risparmio totale = autoconsumo + energia ceduta
 *
 * Tutti i numeri vivono già nelle `derivations` del roof — questo
 * componente li mostra solo. NO calcoli lato client.
 *
 * Visibilità:
 *  - Solo se abbiamo `realistic_yearly_savings_eur` (sector-aware
 *    estimator ha funzionato).
 *  - Solo se almeno 3 dei 5 campi del breakdown sono presenti.
 */

import { useState } from 'react';

type Props = {
  brandColor: string;
  productionKwh: number | null | undefined;
  consumptionKwh: number | null | undefined;
  consumptionMethod?: string | null;
  selfKwh: number | null | undefined;
  exportKwh: number | null | undefined;
  totalSavingsEur: number | null | undefined;
  gridPricePerKwh?: number; // default 0.22 (B2B)
  exportPricePerKwh?: number; // default 0.09 (RID)
};

const SECTOR_LABELS: Record<string, string> = {
  sector_industry_heavy: 'manifatturiero pesante',
  sector_industry_light: 'manifatturiero leggero',
  sector_food_production: 'produzione alimentare',
  sector_logistics: 'logistica',
  sector_retail_gdo: 'grande distribuzione',
  sector_horeca: 'HoReCa',
  sector_hospitality_large: 'ricettivo grande',
  sector_hospitality_food_service: 'ristorazione collettiva',
  sector_healthcare: 'sanitario',
  sector_healthcare_private: 'sanitario privato',
  sector_agricultural_intensive: 'agricolo intensivo',
  sector_automotive: 'automotive',
  sector_education: 'istruzione',
  sector_personal_services: 'servizi alla persona',
  sector_professional_offices: 'studi professionali',
  b2c_household_2700kwh: 'residenziale (consumo medio italiano)',
  fallback_generic: 'commerciale generico',
};

function formatKwh(n: number | null | undefined): string {
  if (n == null) return '—';
  return Math.round(n).toLocaleString('it-IT');
}

function formatEur(n: number | null | undefined): string {
  if (n == null) return '—';
  return Math.round(n).toLocaleString('it-IT');
}

export function BehindTheNumbersPanel({
  brandColor,
  productionKwh,
  consumptionKwh,
  consumptionMethod,
  selfKwh,
  exportKwh,
  totalSavingsEur,
  gridPricePerKwh = 0.22,
  exportPricePerKwh = 0.09,
}: Props) {
  const [open, setOpen] = useState(false);

  // Gating: serve avere almeno produzione + consumo + total per
  // mostrare la card. Altrimenti non c'è una storia da raccontare.
  const hasEnough =
    totalSavingsEur != null &&
    totalSavingsEur > 0 &&
    productionKwh != null &&
    consumptionKwh != null;
  if (!hasEnough) return null;

  const sectorLabel = consumptionMethod
    ? SECTOR_LABELS[consumptionMethod] ?? 'profilo settore'
    : 'profilo settore';

  // Defensive computes: se selfKwh/exportKwh non arrivano dal backend,
  // li calcoliamo qui (matematica banale, no rischi di drift).
  const sf = selfKwh ?? Math.min(productionKwh!, consumptionKwh!);
  const ex = exportKwh ?? Math.max(0, productionKwh! - consumptionKwh!);
  const selfEur = sf * gridPricePerKwh;
  const exportEur = ex * exportPricePerKwh;
  const computedTotal = selfEur + exportEur;

  return (
    <section className="mx-auto max-w-6xl px-6 pb-6">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="group flex w-full items-center justify-between gap-3 rounded-xl bg-surface-container-low px-5 py-3 text-left transition hover:bg-surface-container"
        aria-expanded={open}
      >
        <span className="flex items-center gap-2.5">
          <span className="text-lg" aria-hidden>
            🧮
          </span>
          <span className="text-sm font-semibold text-on-surface">
            Da dove vengono questi numeri
          </span>
          <span className="text-xs text-on-surface-variant">
            (matematica trasparente)
          </span>
        </span>
        <span
          aria-hidden
          className="inline-block text-on-surface-variant transition-transform"
          style={{ transform: open ? 'rotate(180deg)' : 'rotate(0deg)' }}
        >
          ▾
        </span>
      </button>

      {open && (
        <div className="mt-2 rounded-xl bg-surface-container-lowest p-5 md:p-6">
          <ol className="space-y-3 text-sm">
            <Row
              num={1}
              label="Produzione attesa dal tuo tetto"
              value={`${formatKwh(productionKwh)} kWh/anno`}
              source="Google Solar API · geometria del tuo tetto, esposizione e ombre"
            />
            <Row
              num={2}
              label={`Consumo stimato per la tua attività (${sectorLabel})`}
              value={`${formatKwh(consumptionKwh)} kWh/anno`}
              source="Stima basata sul consumo medio del settore moltiplicato per la superficie del tetto"
            />
            <Row
              num={3}
              label="Energia che usi tu (autoconsumo)"
              value={`${formatKwh(sf)} kWh × ${gridPricePerKwh.toLocaleString('it-IT', { minimumFractionDigits: 2 })}€ = €${formatEur(selfEur)}`}
              source="Quello che produci e consumi nello stesso momento: risparmi al prezzo della rete"
              accentColor={brandColor}
            />
            {ex > 0 && (
              <Row
                num={4}
                label="Energia ceduta alla rete (RID)"
                value={`${formatKwh(ex)} kWh × ${exportPricePerKwh.toLocaleString('it-IT', { minimumFractionDigits: 2 })}€ = €${formatEur(exportEur)}`}
                source="Quello che produci in più viene venduto al GSE a tariffa Ritiro Dedicato"
                accentColor={brandColor}
              />
            )}
            <li
              className="mt-2 rounded-lg p-4 text-base"
              style={{
                backgroundColor: `${brandColor}12`,
                border: `1px solid ${brandColor}30`,
              }}
            >
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-headline text-base font-bold tracking-tight text-on-surface">
                  ➕ Risparmio totale annuo
                </span>
                <span
                  className="font-headline text-2xl font-bold tracking-tight"
                  style={{ color: brandColor }}
                >
                  € {formatEur(totalSavingsEur ?? computedTotal)}
                </span>
              </div>
            </li>
          </ol>

          <p className="mt-4 text-[11px] leading-relaxed text-on-surface-variant">
            Stima indicativa basata su benchmark di settore (ARERA, ENEA).
            Caricando la tua bolletta reale qui sotto i numeri vengono
            ricalcolati sul tuo profilo effettivo di consumo e sulla
            tariffa che paghi.
          </p>
        </div>
      )}
    </section>
  );
}

function Row({
  num,
  label,
  value,
  source,
  accentColor,
}: {
  num: number;
  label: string;
  value: string;
  source: string;
  accentColor?: string;
}) {
  return (
    <li className="flex gap-3">
      <span
        aria-hidden
        className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold"
        style={{
          backgroundColor: accentColor ? `${accentColor}20` : 'var(--surface-container)',
          color: accentColor ?? 'inherit',
        }}
      >
        {num}
      </span>
      <div className="flex-1">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span className="text-sm font-medium text-on-surface">{label}</span>
          <span className="font-headline text-sm font-semibold tabular-nums text-on-surface">
            {value}
          </span>
        </div>
        <p className="mt-0.5 text-xs text-on-surface-variant">{source}</p>
      </div>
    </li>
  );
}
