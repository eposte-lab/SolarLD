'use client';

/**
 * BollettaTimelineRow — riga "premium" per l'evento
 * ``portal.bolletta_uploaded`` nella timeline "Cosa ha fatto sul portale".
 *
 * Caricare la bolletta è il segnale ad altissima intenzione del funnel:
 * la riga ha un trattamento speciale (anello mint + alone
 * ``shadow-editorial-glow`` + shimmer ``animate-liquid-shine``) e, al
 * click, scrolla alla ``BollettaCard`` (``#bolletta-card``) dove l'operatore
 * vede il risparmio EPC annuale e può aprire/scaricare il documento.
 *
 * Client component (serve ``onClick``); riceve già label/detail/tempo dal
 * server component padre.
 */

import { Receipt } from 'lucide-react';

function scrollToBolletta() {
  const el = document.getElementById('bolletta-card');
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  // Lampeggio breve per orientare lo sguardo dopo lo scroll.
  el.classList.add('ring-2', 'ring-primary');
  window.setTimeout(() => el.classList.remove('ring-2', 'ring-primary'), 1600);
}

export function BollettaTimelineRow({
  label,
  detail,
  at,
}: {
  label: string;
  detail: string | null;
  at: string;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={scrollToBolletta}
        className="relative flex w-full items-center gap-3 overflow-hidden rounded-lg bg-primary/10 px-4 py-3 text-left shadow-editorial-glow ring-1 ring-primary/40 transition-transform hover:scale-[1.01]"
        title="Vai alla bolletta caricata"
      >
        {/* Shimmer aura */}
        <span
          aria-hidden
          className="pointer-events-none absolute inset-0 animate-liquid-shine bg-gradient-to-r from-transparent via-primary/20 to-transparent"
        />
        <span className="relative inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-on-primary">
          <Receipt size={14} aria-hidden />
        </span>
        <div className="relative min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-on-surface">
            {label}
          </p>
          {detail && (
            <p className="truncate text-xs text-on-surface-variant">{detail}</p>
          )}
        </div>
        <span className="relative shrink-0 text-xs font-medium text-primary">
          {at}
        </span>
      </button>
    </li>
  );
}
