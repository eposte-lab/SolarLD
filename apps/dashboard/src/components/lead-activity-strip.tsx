/**
 * LeadActivityStrip — funnel timeline del lead, stile Resend / "verify
 * your domain": una traccia orizzontale a piena larghezza con nodi a
 * pillola gradient e connettori che si "accendono" tra step completati.
 *
 * Step: inviata → letta → cliccata → portale → bolletta → appuntamento.
 *
 * Note di design:
 * - Layout a grid di 6 colonne UGUALI: nodi e label condividono lo
 *   stesso asse verticale di centro-cella, quindi primo e ultimo step
 *   restano perfettamente allineati come quelli intermedi (era il bug
 *   del flex precedente).
 * - Nodi 44 px, riempiti con un gradient brand quando lo step è
 *   raggiunto, contorno tratteggiato neutro quando è ancora pending.
 * - Il nodo "current" (ultimo completato) ha un halo pulsante: dà la
 *   sensazione "siamo qui adesso".
 * - I connettori sono linee da 2 px con gradient brand quando ENTRAMBI
 *   gli step adiacenti sono done, neutri (track grigio) quando almeno
 *   uno è pending.
 * - Niente larghezza fissa: lo strip si estende per tutta la larghezza
 *   del contenitore (in `page.tsx` rimosso il vecchio `max-w-2xl`).
 */

import {
  CalendarCheck,
  Check,
  FileText,
  Globe,
  MailCheck,
  MousePointerClick,
  Send,
} from 'lucide-react';

import { relativeTime } from '@/lib/utils';

export interface LeadActivityFlags {
  outreachSentAt: string | null;
  outreachOpenedAt: string | null;
  outreachClickedAt: string | null;
  portalVisitedAt: string | null;
  bollettaUploadedAt: string | null;
  appointmentRequestedAt: string | null;
}

interface PillSpec {
  key: string;
  label: string;
  Icon: typeof Send;
  at: string | null;
  active: boolean;
}

export function LeadActivityStrip({
  flags,
  className,
}: {
  flags: LeadActivityFlags;
  className?: string;
}) {
  const pills: PillSpec[] = [
    {
      key: 'sent',
      label: 'Inviata',
      Icon: Send,
      at: flags.outreachSentAt,
      active: flags.outreachSentAt != null,
    },
    {
      key: 'opened',
      label: 'Letta',
      Icon: MailCheck,
      at: flags.outreachOpenedAt,
      active: flags.outreachOpenedAt != null,
    },
    {
      key: 'clicked',
      label: 'Cliccata',
      Icon: MousePointerClick,
      at: flags.outreachClickedAt,
      active: flags.outreachClickedAt != null,
    },
    {
      key: 'portal',
      label: 'Portale',
      Icon: Globe,
      at: flags.portalVisitedAt,
      active: flags.portalVisitedAt != null,
    },
    {
      key: 'bolletta',
      label: 'Bolletta',
      Icon: FileText,
      at: flags.bollettaUploadedAt,
      active: flags.bollettaUploadedAt != null,
    },
    {
      key: 'appointment',
      label: 'Appuntamento',
      Icon: CalendarCheck,
      at: flags.appointmentRequestedAt,
      active: flags.appointmentRequestedAt != null,
    },
  ];

  // Funnel MONOTÒNO. Il tracking a monte è inaffidabile per natura: il
  // pixel di apertura viene bloccato da Outlook / Apple Mail Privacy, e
  // un click può non passare dal redirect tracciato (es. il lead apre il
  // portale da un link diretto). Risultato: capita di registrare
  // "Portale visitato" senza "Letta"/"Cliccata" — uno stato impossibile
  // (per arrivare al portale DEVI aver aperto e cliccato).
  //
  // Quindi un nodo è "done" se il suo evento è arrivato O se ne è
  // arrivato uno a valle nella catena causale. La catena email è
  // strettamente sequenziale fino al portale (inviata → letta → cliccata
  // → portale): qualunque step successivo implica tutti i precedenti.
  // Bolletta e Appuntamento sono invece "azioni di portale" indipendenti
  // tra loro (si può fissare un appuntamento senza caricare la bolletta),
  // perciò NON si accendono a vicenda — restano foglie, accese solo dal
  // proprio evento (ma implicano comunque il portale, indice ≤ PORTAL).
  const PORTAL_IDX = pills.findIndex((p) => p.key === 'portal');
  const doneFlags = pills.map((p, i) =>
    i <= PORTAL_IDX ? pills.slice(i).some((s) => s.active) : p.active,
  );

  // Indice dell'ultimo step completato (monotòno) — riceve l'halo "current".
  const lastDoneIdx = doneFlags.reduce<number>(
    (acc, d, i) => (d ? i : acc),
    -1,
  );

  return (
    <div className={`relative w-full ${className ?? ''}`}>
      {/* Keyframes locali: halo che pulsa attorno al nodo "current". */}
      <style>
        {`
          @keyframes lasCurrentHalo {
            0%, 100% { transform: scale(1);   opacity: 0.55; }
            50%      { transform: scale(1.35); opacity: 0;   }
          }
          @keyframes lasNodeIn {
            0%   { transform: scale(0.85); opacity: 0; }
            100% { transform: scale(1);    opacity: 1; }
          }
        `}
      </style>

      <ol
        className="grid grid-cols-6 items-start"
        aria-label="Funnel di interazione del lead"
      >
        {pills.map((p, idx) => {
          const isFirst = idx === 0;
          const isLast = idx === pills.length - 1;
          const done = doneFlags[idx] ?? false;
          const nextDone = !isLast && (doneFlags[idx + 1] ?? false);
          const prevDone = !isFirst && (doneFlags[idx - 1] ?? false);
          const isCurrent = idx === lastDoneIdx;
          // Step raggiunto per implicazione (a valle) ma senza un proprio
          // timestamp: niente "in attesa", ma nemmeno un orario inventato.
          const impliedOnly = done && p.at == null;

          // Connettore sinistro: pieno-brand quando precedente E corrente
          // sono done, half-fade quando solo precedente, neutro altrimenti.
          const leftFilled = prevDone && done;
          const leftHalf = prevDone && !done; // gradient fade verso il neutro
          // Connettore destro: simmetrico.
          const rightFilled = done && nextDone;
          const rightHalf = done && !nextDone;

          // Lo step "bolletta", quando raggiunto, è cliccabile: porta alla
          // BollettaCard (#bolletta-card) dove l'operatore vede il
          // risparmio EPC annuale e può aprire/scaricare il documento.
          // Anchor nativo → niente 'use client' su questo server component.
          const isBollettaLink = p.key === 'bolletta' && done;

          return (
            <li
              key={p.key}
              className="relative flex flex-col items-center"
            >
              {isBollettaLink && (
                <a
                  href="#bolletta-card"
                  aria-label="Vai alla bolletta caricata"
                  title="Vai alla bolletta caricata"
                  className="absolute inset-0 z-20 rounded-lg"
                />
              )}
              {/* === RIGA DEI CONNETTORI + NODO === */}
              <div className="relative flex h-12 w-full items-center justify-center">
                {/* Connettore sinistro (dalla metà sinistra della cella
                    al centro). Nascosto sul primo step. */}
                {!isFirst && (
                  <span
                    aria-hidden
                    className="absolute left-0 right-1/2 top-1/2 h-[2px] -translate-y-1/2"
                    style={{
                      background: leftFilled
                        ? 'linear-gradient(90deg, var(--brand-mint, #6FCF97) 0%, var(--brand-mint, #6FCF97) 100%)'
                        : leftHalf
                          ? 'linear-gradient(90deg, var(--brand-mint, #6FCF97) 0%, rgba(255,255,255,0.10) 100%)'
                          : 'rgba(255,255,255,0.10)',
                      boxShadow: leftFilled
                        ? '0 0 12px rgba(111,207,151,0.45)'
                        : undefined,
                    }}
                  />
                )}
                {/* Connettore destro (dal centro al bordo destro).
                    Nascosto sull'ultimo step. */}
                {!isLast && (
                  <span
                    aria-hidden
                    className="absolute left-1/2 right-0 top-1/2 h-[2px] -translate-y-1/2"
                    style={{
                      background: rightFilled
                        ? 'linear-gradient(90deg, var(--brand-mint, #6FCF97) 0%, var(--brand-mint, #6FCF97) 100%)'
                        : rightHalf
                          ? 'linear-gradient(90deg, var(--brand-mint, #6FCF97) 0%, rgba(255,255,255,0.10) 100%)'
                          : 'rgba(255,255,255,0.10)',
                      boxShadow: rightFilled
                        ? '0 0 12px rgba(111,207,151,0.45)'
                        : undefined,
                    }}
                  />
                )}

                {/* Halo pulsante sul nodo "current" — l'ultimo step
                    completato. Dà l'idea di "stato attivo adesso". */}
                {isCurrent && (
                  <span
                    aria-hidden
                    className="pointer-events-none absolute h-11 w-11 rounded-full"
                    style={{
                      backgroundColor: 'rgba(111,207,151,0.45)',
                      animation: 'lasCurrentHalo 2s ease-in-out infinite',
                    }}
                  />
                )}

                {/* Nodo */}
                <span
                  title={
                    impliedOnly
                      ? `${p.label} · raggiunta`
                      : done
                        ? `${p.label} · ${relativeTime(p.at)}`
                        : `${p.label} · non ancora`
                  }
                  className={`relative z-10 inline-flex h-11 w-11 items-center justify-center rounded-full transition-all duration-300 ${
                    done
                      ? 'text-white shadow-[0_4px_18px_rgba(111,207,151,0.35)]'
                      : 'border-2 border-dashed border-on-surface/20 bg-surface-container-low text-on-surface-variant/45'
                  }`}
                  style={
                    done
                      ? {
                          background:
                            'linear-gradient(135deg, var(--brand-mint, #6FCF97) 0%, var(--brand-mint-dark, #4FAE7A) 100%)',
                          animation: 'lasNodeIn 0.4s ease-out',
                        }
                      : undefined
                  }
                >
                  <p.Icon
                    size={17}
                    strokeWidth={done ? 2.4 : 1.8}
                    aria-hidden
                  />
                  {/* Mini check d'angolo per step done che NON sono il
                      current (decoratore "completed"). Sul current
                      l'icona del passo basta + l'halo. */}
                  {done && !isCurrent && (
                    <span
                      aria-hidden
                      className="absolute -bottom-0.5 -right-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full bg-primary text-on-primary ring-2 ring-surface-container-lowest"
                    >
                      <Check size={10} strokeWidth={3} />
                    </span>
                  )}
                </span>
              </div>

              {/* === LABEL + TIMESTAMP === */}
              <span
                className={`mt-3 text-center text-[11px] font-semibold uppercase tracking-[0.08em] ${
                  done ? 'text-on-surface' : 'text-on-surface-variant/55'
                }`}
              >
                {p.label}
              </span>
              <span
                className={`mt-0.5 text-center text-[10px] tabular-nums ${
                  done ? 'text-on-surface-variant' : 'text-on-surface-variant/40'
                }`}
              >
                {impliedOnly
                  ? 'raggiunta'
                  : done
                    ? relativeTime(p.at)
                    : 'in attesa'}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
