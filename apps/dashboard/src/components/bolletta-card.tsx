'use client';

/**
 * BollettaCard — scheda "Bolletta caricata" nella pagina lead della
 * dashboard interna.
 *
 * Quando un lead carica la propria bolletta è il segnale ad altissima
 * intenzione del funnel: ha condiviso un documento reale e il sistema può
 * calcolare il risparmio EPC *vero* (non una media di settore). Questa card:
 *
 *   • Mostra il confronto ANNUALE — "Oggi paga €X/anno" → "Con l'EPC
 *     €Y/anno (−Z%)" + "Risparmio €W/anno · €V in 10 anni". Stessi numeri
 *     che il lead vede nel dossier (`SavingsComparePanel`, modello EPC),
 *     calcolati server-side da `compute_epc_annual`.
 *   • Anteprima del file (immagine cliccabile o chip PDF) con pulsanti
 *     **Apri** (nuova scheda) e **Scarica**. Solo lettura: nessuna
 *     modifica/eliminazione (la bolletta contiene dati personali).
 *
 * Aura "premium": `shadow-editorial-glow` (alone mint) + overlay
 * `animate-liquid-shine` per dare risalto al segnale.
 *
 * Si auto-fetcha al mount via `api.get('/v1/leads/{leadId}/bolletta')`
 * (JWT iniettato automaticamente). `available:false` → non renderizza nulla.
 */

import { Download, ExternalLink, FileText, Receipt } from 'lucide-react';
import { useEffect, useState } from 'react';

import { api } from '@/lib/api-client';
import { cn, formatEurPlain, formatNumber } from '@/lib/utils';

interface BollettaResponse {
  available: boolean;
  reason?: string;
  signed_url?: string | null;
  file_kind?: 'image' | 'pdf' | 'file';
  source?: string;
  uploaded_at?: string | null;
  bill?: { kwh: number; eur: number };
  epc?: {
    current_annual_eur: number;
    epc_annual_eur: number;
    saving_annual_eur: number;
    pct_off: number;
    saving_10y_eur: number;
  };
}

export function BollettaCard({ leadId }: { leadId: string }) {
  const [data, setData] = useState<BollettaResponse | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let active = true;
    api
      .get<BollettaResponse>(`/v1/leads/${leadId}/bolletta`)
      .then((res) => {
        if (active) setData(res);
      })
      .catch(() => {
        if (active) setData({ available: false });
      })
      .finally(() => {
        if (active) setLoaded(true);
      });
    return () => {
      active = false;
    };
  }, [leadId]);

  // Niente bolletta (o errore) → la card non occupa spazio.
  if (!loaded || !data || !data.available) return null;

  const epc = data.epc;
  const bill = data.bill;
  const fileKind = data.file_kind ?? 'file';
  const signedUrl = data.signed_url ?? undefined;

  return (
    <section
      id="bolletta-card"
      className="relative scroll-mt-24 overflow-hidden rounded-2xl bg-surface-container-lowest p-5 shadow-editorial-glow ring-1 ring-primary/30 md:p-6"
    >
      {/* Aura: shimmer mint che attraversa la card */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 animate-liquid-shine bg-gradient-to-r from-transparent via-primary/10 to-transparent"
      />

      <div className="relative">
        {/* Header */}
        <div className="flex items-center gap-2.5">
          <span className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-primary-container text-on-primary-container">
            <Receipt size={16} strokeWidth={2.25} aria-hidden />
          </span>
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-primary">
              Bolletta caricata
            </p>
            <h2 className="font-headline text-lg font-bold text-on-surface">
              Il suo risparmio reale con l&apos;EPC
            </h2>
          </div>
        </div>

        {/* Confronto annuale: oggi → con l'EPC */}
        {epc ? (
          <>
            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="rounded-xl bg-surface-container p-4">
                <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  Oggi paga
                </p>
                <p className="mt-1.5 font-headline text-2xl font-bold tracking-tight text-on-surface md:text-3xl">
                  {formatEurPlain(epc.current_annual_eur)}
                  <span className="ml-1 text-sm font-medium text-on-surface-variant">
                    /anno
                  </span>
                </p>
              </div>
              <div className="rounded-xl bg-primary/10 p-4 ring-1 ring-primary/30">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-primary">
                    Con l&apos;EPC paga
                  </p>
                  {epc.pct_off > 0 ? (
                    <span className="rounded-full bg-primary px-2 py-0.5 text-[10px] font-bold text-on-primary">
                      −{epc.pct_off}%
                    </span>
                  ) : null}
                </div>
                <p className="mt-1.5 font-headline text-2xl font-bold tracking-tight text-primary md:text-3xl">
                  {formatEurPlain(epc.epc_annual_eur)}
                  <span className="ml-1 text-sm font-medium text-on-surface-variant">
                    /anno
                  </span>
                </p>
              </div>
            </div>

            {/* Riga risparmio */}
            <div className="mt-3 rounded-xl bg-primary/5 p-4 ring-1 ring-primary/20">
              <p className="text-sm text-on-surface">
                Risparmio{' '}
                <strong className="text-primary">
                  {formatEurPlain(epc.saving_annual_eur)}/anno
                </strong>{' '}
                in bolletta, con zero investimento. In 10 anni di contratto
                sono{' '}
                <strong className="text-primary">
                  {formatEurPlain(epc.saving_10y_eur)}
                </strong>
                .
              </p>
              {bill ? (
                <p className="mt-1.5 text-xs text-on-surface-variant">
                  Consumo dichiarato in bolletta:{' '}
                  {formatNumber(Math.round(bill.kwh))} kWh/anno ·{' '}
                  {formatEurPlain(bill.eur)}/anno.
                </p>
              ) : null}
            </div>
          </>
        ) : (
          <p className="mt-4 rounded-xl bg-surface-container p-4 text-sm text-on-surface-variant">
            Bolletta caricata. I dati di consumo non sono ancora sufficienti
            per calcolare il risparmio EPC.
          </p>
        )}

        {/* Anteprima file + azioni (solo lettura) */}
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {signedUrl && fileKind === 'image' ? (
            <a
              href={signedUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="block shrink-0 overflow-hidden rounded-lg ring-1 ring-on-surface/10 transition-transform hover:scale-[1.02]"
              title="Apri la bolletta in una nuova scheda"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={signedUrl}
                alt="Anteprima bolletta"
                className="h-20 w-20 object-cover"
              />
            </a>
          ) : signedUrl ? (
            <span className="inline-flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2 text-sm text-on-surface-variant ring-1 ring-on-surface/10">
              <FileText size={16} aria-hidden />
              {fileKind === 'pdf' ? 'Documento PDF' : 'File bolletta'}
            </span>
          ) : null}

          {signedUrl ? (
            <div className="flex flex-wrap items-center gap-2">
              <a
                href={signedUrl}
                target="_blank"
                rel="noopener noreferrer"
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-semibold transition-colors',
                  'bg-primary text-on-primary hover:bg-primary/90',
                )}
              >
                <ExternalLink size={14} strokeWidth={2.25} aria-hidden />
                Apri
              </a>
              <a
                href={signedUrl}
                download
                className="inline-flex items-center gap-1.5 rounded-lg bg-white/[0.06] px-3 py-2 text-sm font-semibold text-on-surface transition-colors hover:bg-white/[0.12]"
              >
                <Download size={14} strokeWidth={2.25} aria-hidden />
                Scarica
              </a>
            </div>
          ) : (
            <p className="text-xs text-on-surface-variant">
              Anteprima del documento non disponibile.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
