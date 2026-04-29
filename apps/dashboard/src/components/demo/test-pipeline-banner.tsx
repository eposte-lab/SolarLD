/**
 * Customer-facing "Avvia test pipeline" banner — demo tenant only.
 *
 * What the prospect sees:
 *   ┌──────────────────────────────────────────────────────────────────────┐
 *   │ 🚀  Prova il sistema con un'azienda che conosci      Tentativi: 3/3 │
 *   │     Inserisci P.IVA, indirizzo e email destinatario:                 │
 *   │     ricevi un'email reale generata in ~90 secondi.        [Avvia]    │
 *   └──────────────────────────────────────────────────────────────────────┘
 *
 * The whole banner is a server component — only the dialog button is
 * client-side because it needs to open / close a modal. We pre-resolve
 * `attemptsRemaining` from `getCurrentTenantContext()` on `/leads` so
 * SSR shows the right counter on first paint without a client refetch.
 *
 * When `attemptsRemaining === 0` we render a quieter "esauriti" variant:
 * same shape, no button, neutral copy. Hiding it entirely would be
 * jarring for a returning prospect — they'd wonder if they imagined
 * the feature.
 */

import { Rocket } from 'lucide-react';

import { TestPipelineDialog } from './test-pipeline-dialog';

const TOTAL_ATTEMPTS = 3;

export function TestPipelineBanner({
  attemptsRemaining,
}: {
  attemptsRemaining: number;
}) {
  const exhausted = attemptsRemaining <= 0;

  return (
    <section
      className={
        exhausted
          ? 'flex flex-wrap items-center gap-3 rounded-2xl bg-surface-container-low px-5 py-4 shadow-ambient-sm'
          : 'flex flex-wrap items-center gap-4 rounded-2xl bg-gradient-to-r from-primary/10 via-surface-container-lowest to-surface-container-lowest px-5 py-4 shadow-ambient-md ring-1 ring-primary/15'
      }
      aria-label="Test pipeline demo"
    >
      <div
        className={
          exhausted
            ? 'flex h-10 w-10 items-center justify-center rounded-full bg-surface-container-high text-on-surface-variant'
            : 'flex h-10 w-10 items-center justify-center rounded-full bg-primary/15 text-primary'
        }
        aria-hidden
      >
        <Rocket size={18} strokeWidth={2.25} />
      </div>

      <div className="flex flex-1 flex-col gap-0.5">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
          {exhausted ? 'Test pipeline · esauriti' : 'Demo · Test pipeline'}
        </p>
        {exhausted ? (
          <p className="text-sm text-on-surface">
            Hai usato tutti i {TOTAL_ATTEMPTS} tentativi. Continua a esplorare i
            lead generati o contattaci per estendere la prova.
          </p>
        ) : (
          <p className="text-sm text-on-surface">
            <span className="font-semibold">
              Prova il sistema con un&apos;azienda che conosci.
            </span>{' '}
            Inserisci P.IVA, indirizzo e email destinatario: ricevi un&apos;email
            reale in ~90 secondi.
          </p>
        )}
      </div>

      <div className="flex items-center gap-3">
        {/* Counter pill — neutral when exhausted, primary when usable */}
        <span
          className={
            exhausted
              ? 'rounded-full bg-surface-container-high px-3 py-1 text-xs font-semibold text-on-surface-variant'
              : 'rounded-full bg-primary/15 px-3 py-1 text-xs font-semibold text-primary'
          }
          title="Tentativi rimanenti per il test pipeline"
        >
          Tentativi: {attemptsRemaining}/{TOTAL_ATTEMPTS}
        </span>

        {!exhausted && <TestPipelineDialog attemptsRemaining={attemptsRemaining} />}
      </div>
    </section>
  );
}
