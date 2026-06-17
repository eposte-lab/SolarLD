'use client';

/**
 * RealisticSizingBackfillCard — operator tool to recompute existing leads'
 * quote numbers under the realistic-sizing trim (drop Google's scattered
 * max-array fill → keep the main roof planes).
 *
 * Two-step + safe: "Anteprima" runs the backfill in dry_run mode (no writes,
 * shows the average kWp drop + per-roof samples); "Applica" re-runs with
 * dry_run=false. Targets ready_to_send leads first. Backend:
 * POST /v1/leads/backfill-realistic-sizing (pure Python, no Solar/Replicate
 * spend; the AI marketing render is untouched).
 */

import { useState } from 'react';
import { AlertTriangle, Check, Ruler, X } from 'lucide-react';

import { api, ApiError } from '@/lib/api-client';
import { BentoCard } from '@/components/ui/bento-card';

type Sample = {
  roof_id: string;
  kwp_old: number;
  kwp_new: number;
  yearly_savings_eur: number | null;
};
type Preview = {
  roofs_considered: number;
  avg_kwp_old: number | null;
  avg_kwp_new: number | null;
  avg_pct_drop: number | null;
  skipped: number;
  samples: Sample[];
};
type ApplyResult = { roofs_changed: number; leads_updated: number; skipped: number };

type State =
  | { kind: 'idle' }
  | { kind: 'previewing' }
  | { kind: 'preview'; data: Preview }
  | { kind: 'applying'; data: Preview }
  | { kind: 'applied'; result: ApplyResult }
  | { kind: 'error'; message: string };

const ENDPOINT = '/v1/leads/backfill-realistic-sizing?target=ready_to_send';

export function RealisticSizingBackfillCard() {
  const [state, setState] = useState<State>({ kind: 'idle' });

  async function run<T>(dryRun: boolean): Promise<T> {
    return api.post<T>(`${ENDPOINT}&dry_run=${dryRun}`, {});
  }

  async function preview() {
    setState({ kind: 'previewing' });
    try {
      setState({ kind: 'preview', data: await run<Preview>(true) });
    } catch (err) {
      setState({
        kind: 'error',
        message: err instanceof ApiError ? err.message : 'Errore di rete.',
      });
    }
  }

  async function apply(prev: Preview) {
    setState({ kind: 'applying', data: prev });
    try {
      setState({ kind: 'applied', result: await run<ApplyResult>(false) });
    } catch (err) {
      setState({
        kind: 'error',
        message: err instanceof ApiError ? err.message : 'Errore di rete.',
      });
    }
  }

  return (
    <BentoCard span="full">
      <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
        Manutenzione · solo super_admin
      </p>
      <h2 className="mt-1 font-headline text-2xl font-bold tracking-tighter">
        Dimensionamento realistico
      </h2>
      <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
        Ricalcola kWp e risparmio dei lead esistenti tenendo solo le falde
        principali del tetto (la Solar API riempie ogni minima struttura →
        numeri gonfiati). Niente costo: l&apos;immagine commerciale non cambia,
        solo i numeri. Parte dai lead <strong>pronti all&apos;invio</strong>.
      </p>

      {(state.kind === 'idle' || state.kind === 'previewing') && (
        <div className="mt-5">
          <button
            type="button"
            onClick={preview}
            disabled={state.kind === 'previewing'}
            className="inline-flex items-center gap-2 rounded-lg bg-surface-container-highest px-5 py-2.5 text-sm font-semibold text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-60"
          >
            <Ruler size={14} strokeWidth={2.25} aria-hidden />
            {state.kind === 'previewing' ? 'Calcolo anteprima…' : 'Anteprima (nessuna modifica)'}
          </button>
        </div>
      )}

      {(state.kind === 'preview' || state.kind === 'applying') && (
        <div className="mt-5 space-y-4">
          <div className="flex flex-wrap items-end gap-x-8 gap-y-3 rounded-xl bg-surface-container-low p-4">
            <Stat label="Lead considerati" value={String(state.data.roofs_considered)} />
            <Stat
              label="kWp medio"
              value={`${state.data.avg_kwp_old ?? '—'} → ${state.data.avg_kwp_new ?? '—'}`}
            />
            <Stat
              label="Calo medio"
              value={state.data.avg_pct_drop != null ? `−${state.data.avg_pct_drop}%` : '—'}
              accent
            />
          </div>

          {state.data.samples.length > 0 && (
            <div className="overflow-x-auto rounded-xl bg-surface-container-low">
              <table className="w-full min-w-[420px] text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-widest text-on-surface-variant">
                    <th className="px-4 py-2">Roof</th>
                    <th className="px-4 py-2 text-right">kWp ora</th>
                    <th className="px-4 py-2 text-right">kWp nuovo</th>
                    <th className="px-4 py-2 text-right">€/anno nuovo</th>
                  </tr>
                </thead>
                <tbody>
                  {state.data.samples.slice(0, 8).map((s) => (
                    <tr key={s.roof_id} className="border-t border-outline-variant/40">
                      <td className="px-4 py-2 font-mono text-[11px] text-on-surface-variant">
                        {s.roof_id.slice(0, 8)}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums text-on-surface-variant">
                        {s.kwp_old}
                      </td>
                      <td className="px-4 py-2 text-right font-semibold tabular-nums">{s.kwp_new}</td>
                      <td className="px-4 py-2 text-right tabular-nums">
                        {s.yearly_savings_eur != null
                          ? `€${Math.round(s.yearly_savings_eur).toLocaleString('it-IT')}`
                          : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => apply(state.data)}
              disabled={state.kind === 'applying'}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-semibold text-on-primary transition-opacity hover:opacity-90 disabled:opacity-60"
            >
              <AlertTriangle size={14} strokeWidth={2.25} aria-hidden />
              {state.kind === 'applying'
                ? 'Applico…'
                : 'Applica ai pronti all’invio (scrive i numeri)'}
            </button>
            <button
              type="button"
              onClick={() => setState({ kind: 'idle' })}
              disabled={state.kind === 'applying'}
              className="text-xs font-semibold text-on-surface-variant hover:underline"
            >
              Annulla
            </button>
          </div>
        </div>
      )}

      {state.kind === 'applied' && (
        <div className="mt-5 inline-flex items-start gap-2 rounded-xl bg-success-container px-4 py-3 text-sm text-on-success-container">
          <Check size={16} strokeWidth={2.5} className="mt-0.5 shrink-0" aria-hidden />
          <span>
            Fatto: <strong>{state.result.roofs_changed}</strong> tetti ricalcolati,{' '}
            <strong>{state.result.leads_updated}</strong> lead aggiornati
            {state.result.skipped ? ` · ${state.result.skipped} saltati` : ''}. I dossier
            riflettono i nuovi numeri.
          </span>
        </div>
      )}

      {state.kind === 'error' && (
        <div className="mt-5 inline-flex items-start gap-2 rounded-xl bg-error-container px-4 py-3 text-sm text-on-error-container">
          <X size={16} strokeWidth={2.5} className="mt-0.5 shrink-0" aria-hidden />
          <span>{state.message}</span>
        </div>
      )}
    </BentoCard>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
        {label}
      </p>
      <p
        className={
          'mt-0.5 font-headline text-xl font-bold tabular-nums ' +
          (accent ? 'text-primary' : 'text-on-surface')
        }
      >
        {value}
      </p>
    </div>
  );
}
