/**
 * Geocentric Autopilot — single-card UX for /territorio.
 *
 * Replaces the legacy config + L0 + scan-trigger panels with an
 * autonomous flow:
 *
 *   1. On mount, calls /v1/territory/auto-prepare. The endpoint is
 *      idempotent and decides whether to enqueue OSM mapping (L0),
 *      the L1→L3 scan, or do nothing because the pool is already
 *      ready.
 *   2. Polls /v1/territory/scan-results every 8 seconds while a scan
 *      is in flight to surface stage-by-stage progress.
 *   3. When candidates exist, lists them with a "Qualifica" button per
 *      row that runs the paid stages (L4 Solar + L5 Haiku + L6 lead
 *      creation) for that single candidate.
 *   4. The "Riparti da capo" button calls /v1/territory/reset and then
 *      re-triggers auto-prepare.
 *
 * The hard 10-lead cap lives server-side in /candidates/{id}/qualify;
 * this UI just surfaces the cap state via `qualified_count / target_total`.
 */

'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';

import {
  autoPrepareTerritory,
  getScanResults,
  qualifyCandidate,
  resetTerritoryPipeline,
  type ScanResultsResponse,
} from '@/lib/data/territory';

const POLL_INTERVAL_MS = 8_000;

interface AutopilotProps {
  initialData: ScanResultsResponse | null;
}

interface RowState {
  busy: boolean;
  result?: {
    success: boolean;
    message: string;
    score: number | null;
    leadId: string | null;
  };
}

export function TerritorioAutopilot({ initialData }: AutopilotProps) {
  const [data, setData] = useState<ScanResultsResponse | null>(initialData);
  const [note, setNote] = useState<string>('');
  const [bootstrapping, setBootstrapping] = useState<boolean>(true);
  const [resetBusy, setResetBusy] = useState<boolean>(false);
  const [rowState, setRowState] = useState<Record<string, RowState>>({});
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Derived: pool of candidates eligible for selective qualification.
  const pool = useMemo(() => {
    if (!data) return [];
    return data.top_candidates.filter(
      (c) => (c.building_quality_score ?? 0) >= 3,
    );
  }, [data]);

  const summary = data?.summary;
  const isPreparing =
    !!summary &&
    (summary.is_running || (summary.l3_accepted === 0 && summary.l1_candidates === 0));

  const qualifiedCount = summary?.l6_leads_created ?? 0;
  const targetTotal = 10;
  const capReached = qualifiedCount >= targetTotal;

  // Fetcher used by both polling and post-action refresh.
  const refresh = useCallback(async () => {
    try {
      const next = await getScanResults();
      setData(next);
    } catch (err) {
      // best-effort — leave previous data in place
      console.warn('scan-results poll failed', err);
    }
  }, []);

  // Bootstrap: fire auto-prepare once on mount, then start polling.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await autoPrepareTerritory();
        if (cancelled) return;
        setNote(res.note);
      } catch (err) {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : 'auto-prepare failed';
        setErrorMsg(msg);
      } finally {
        if (!cancelled) setBootstrapping(false);
      }
      await refresh();
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  // Poll while the scan is in flight.
  useEffect(() => {
    if (!summary?.is_running && pool.length > 0) return;
    const id = window.setInterval(() => {
      void refresh();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [summary?.is_running, pool.length, refresh]);

  const handleQualify = async (candidateId: string) => {
    setRowState((prev) => ({
      ...prev,
      [candidateId]: { busy: true },
    }));
    try {
      const res = await qualifyCandidate(candidateId);
      setRowState((prev) => ({
        ...prev,
        [candidateId]: {
          busy: false,
          result: {
            success: !!res.lead_id,
            message: res.message,
            score: res.overall_score,
            leadId: res.lead_id,
          },
        },
      }));
      await refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'qualifica fallita';
      setRowState((prev) => ({
        ...prev,
        [candidateId]: {
          busy: false,
          result: { success: false, message: msg, score: null, leadId: null },
        },
      }));
    }
  };

  const handleReset = async () => {
    if (
      !window.confirm(
        'Ripartire da capo? Verranno eliminati candidati e lead generati dal flusso geocentrico v3.',
      )
    ) {
      return;
    }
    setResetBusy(true);
    setErrorMsg(null);
    try {
      await resetTerritoryPipeline();
      setRowState({});
      const res = await autoPrepareTerritory();
      setNote(res.note);
      await refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'reset fallito';
      setErrorMsg(msg);
    } finally {
      setResetBusy(false);
    }
  };

  return (
    <section className="space-y-6">
      <div className="rounded-2xl border border-outline-variant bg-surface-container-low p-6">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-xs uppercase tracking-wider text-on-surface-variant">
              Pipeline geocentrica · autopilota
            </p>
            <h2 className="mt-1 text-xl font-semibold text-on-surface">
              {bootstrapping
                ? 'Inizializzazione…'
                : isPreparing
                  ? 'Preparazione candidati in corso'
                  : pool.length > 0
                    ? `${pool.length} candidati pronti per la qualifica`
                    : 'Nessun candidato disponibile'}
            </h2>
            <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
              {note ||
                "Le fasi L0 (mappatura zone) ed L1→L3 (scoperta + scraping + filtro qualità) girano automaticamente in background. Costo per la fase di preparazione: vicino a zero."}
            </p>
          </div>
          <button
            type="button"
            onClick={handleReset}
            disabled={resetBusy || bootstrapping}
            className="self-start rounded-md border border-outline px-3 py-1.5 text-xs font-medium text-on-surface-variant hover:bg-surface-container-high disabled:opacity-50"
          >
            {resetBusy ? 'Reset in corso…' : 'Riparti da capo'}
          </button>
        </div>

        {errorMsg ? (
          <div className="mt-3 rounded-md border border-error/40 bg-error/10 p-3 text-sm text-error">
            {errorMsg}
          </div>
        ) : null}

        {/* Stage chips */}
        {summary ? (
          <div className="mt-5 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
            <Chip label="L1 Places" value={summary.l1_candidates} />
            <Chip label="L2 con email" value={summary.l2_with_email} />
            <Chip label="L3 qualità ok" value={summary.l3_accepted} />
            <Chip
              label={`Lead finali (target ${targetTotal})`}
              value={qualifiedCount}
              warn={capReached}
            />
          </div>
        ) : null}
      </div>

      <div className="rounded-2xl border border-outline-variant bg-surface-container-low p-6">
        <div className="mb-4 flex items-baseline justify-between">
          <h3 className="text-lg font-semibold text-on-surface">
            Pool candidati L3 · qualifica selettiva
          </h3>
          <p className="text-xs text-on-surface-variant">
            Solar API + Haiku + creazione lead vengono eseguiti solo quando
            premi <strong>Qualifica</strong> sulla riga.
          </p>
        </div>

        {pool.length === 0 ? (
          <div className="rounded-md border border-dashed border-outline-variant p-8 text-center text-sm text-on-surface-variant">
            {isPreparing
              ? 'Scansione in corso. La pagina si aggiorna automaticamente ogni 8 secondi.'
              : 'Nessun candidato L3 ancora disponibile. Riprova fra qualche minuto o usa "Riparti da capo".'}
          </div>
        ) : (
          <ul className="divide-y divide-outline-variant">
            {pool.map((c) => {
              const state = rowState[c.id];
              const alreadyQualified = (c.solar_verdict ?? '') === 'accepted';
              return (
                <li
                  key={c.id}
                  className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium text-on-surface">
                      {c.business_name || c.google_place_id || c.id.slice(0, 8)}
                    </p>
                    <p className="mt-0.5 truncate text-xs text-on-surface-variant">
                      {[
                        c.predicted_sector,
                        c.best_email,
                        c.website,
                        c.phone,
                      ]
                        .filter(Boolean)
                        .join(' · ') || '—'}
                    </p>
                    {state?.result ? (
                      <p
                        className={`mt-1 text-xs ${
                          state.result.success ? 'text-primary' : 'text-on-surface-variant'
                        }`}
                      >
                        {state.result.message}
                        {state.result.leadId ? ` (lead ${state.result.leadId.slice(0, 8)})` : null}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    {alreadyQualified ? (
                      <span className="rounded-full bg-primary/10 px-3 py-1 text-xs font-semibold text-primary">
                        Qualificato {c.overall_score ? `· ${c.overall_score}` : ''}
                      </span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => handleQualify(c.id)}
                        disabled={state?.busy || capReached}
                        className="rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-on-primary hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {state?.busy ? 'Qualifica…' : 'Qualifica'}
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        {capReached ? (
          <p className="mt-4 rounded-md bg-warning/10 p-3 text-xs text-warning">
            Cap raggiunto: già {qualifiedCount} contatti finali qualificati. Usa
            "Riparti da capo" per ricominciare il pilota.
          </p>
        ) : null}
      </div>
    </section>
  );
}

function Chip({
  label,
  value,
  warn = false,
}: {
  label: string;
  value: number;
  warn?: boolean;
}) {
  return (
    <div
      className={`rounded-md border px-3 py-2 ${
        warn
          ? 'border-warning/40 bg-warning/10'
          : 'border-outline-variant bg-surface'
      }`}
    >
      <p className="text-xs uppercase tracking-wider text-on-surface-variant">
        {label}
      </p>
      <p className="mt-0.5 text-lg font-semibold tabular-nums text-on-surface">
        {value.toLocaleString('it-IT')}
      </p>
    </div>
  );
}
