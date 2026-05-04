'use client';

/**
 * Client component for territory actions on the /territorio page.
 *
 * Two actions:
 *   1. "Rimappa il territorio" — POST /v1/territory/map  (L0 OSM zone discovery)
 *   2. "Avvia scansione v3"   — POST /v1/territory/run-funnel (L1→L5 pipeline)
 *
 * Both are async jobs. The UI shows a transient status message with the
 * returned job_id so the operator can track progress in the worker logs.
 */

import { useState } from 'react';

import { mapTerritory, runFunnelManual } from '@/lib/data/territory';

type ActionState = 'idle' | 'busy';

export function TerritorioActions() {
  const [mapState, setMapState] = useState<ActionState>('idle');
  const [mapMsg, setMapMsg] = useState<string | null>(null);
  const [mapErr, setMapErr] = useState<string | null>(null);

  const [funnelState, setFunnelState] = useState<ActionState>('idle');
  const [funnelMsg, setFunnelMsg] = useState<string | null>(null);
  const [funnelErr, setFunnelErr] = useState<string | null>(null);
  const [maxCandidates, setMaxCandidates] = useState<number>(100);

  async function handleRemap() {
    setMapState('busy');
    setMapMsg(null);
    setMapErr(null);
    try {
      const res = await mapTerritory();
      setMapMsg(
        `Mappatura avviata (job ${res.job_id.slice(0, 8)}…) — ` +
          `settori: ${res.wizard_groups.join(', ') || '—'} · ` +
          `province: ${res.province_codes.join(', ') || '—'}.`,
      );
    } catch (e) {
      setMapErr(e instanceof Error ? e.message : 'map_failed');
    } finally {
      setMapState('idle');
    }
  }

  async function handleRunFunnel() {
    setFunnelState('busy');
    setFunnelMsg(null);
    setFunnelErr(null);
    try {
      const res = await runFunnelManual({ max_l1_candidates: maxCandidates });
      setFunnelMsg(
        `Scansione avviata (job ${res.job_id.slice(0, 8)}…) — ` +
          `${res.zone_count} zone · max ${res.max_l1_candidates} candidati. ` +
          `Tempo stimato: ${Math.ceil((res.max_l1_candidates / 100) * 5)}-${Math.ceil((res.max_l1_candidates / 100) * 10)} min.`,
      );
    } catch (e) {
      setFunnelErr(e instanceof Error ? e.message : 'funnel_failed');
    } finally {
      setFunnelState('idle');
    }
  }

  return (
    <div className="space-y-4">
      {/* ---- L0: Remap territory ---- */}
      <div className="space-y-1.5">
        <p className="text-xs font-medium text-on-surface-variant">
          Mappatura OSM (L0)
        </p>
        <button
          type="button"
          onClick={handleRemap}
          disabled={mapState === 'busy'}
          className="rounded-full bg-surface-container-high px-4 py-2 text-sm font-semibold text-on-surface shadow-ambient-sm transition-colors hover:bg-outline-variant disabled:opacity-50"
        >
          {mapState === 'busy' ? 'Mappatura in corso…' : 'Rimappa il territorio'}
        </button>
        {mapMsg ? (
          <p className="text-xs text-success">{mapMsg}</p>
        ) : null}
        {mapErr ? (
          <p className="text-xs text-error">Errore: {mapErr}</p>
        ) : null}
      </div>

      {/* ---- L1→L5: Run funnel ---- */}
      <div className="space-y-1.5">
        <p className="text-xs font-medium text-on-surface-variant">
          Scansione candidati (L1 → L5)
        </p>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleRunFunnel}
            disabled={funnelState === 'busy'}
            className="rounded-full bg-primary px-4 py-2 text-sm font-semibold text-on-primary shadow-ambient-sm transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {funnelState === 'busy' ? 'Scansione in corso…' : 'Avvia scansione v3'}
          </button>
          <div className="flex items-center gap-1.5">
            <label className="text-xs text-on-surface-variant" htmlFor="max-candidates">
              Max candidati:
            </label>
            <select
              id="max-candidates"
              value={maxCandidates}
              onChange={(e) => setMaxCandidates(Number(e.target.value))}
              disabled={funnelState === 'busy'}
              className="rounded-md border border-outline-variant bg-surface-container px-2 py-1 text-xs text-on-surface disabled:opacity-50"
            >
              <option value={50}>50 (test rapido, ~€0.15)</option>
              <option value={100}>100 (pilota, ~€0.30)</option>
              <option value={250}>250 (provinc. piccola, ~€0.75)</option>
              <option value={500}>500 (standard, ~€1.50)</option>
              <option value={1000}>1000 (completo, ~€3.00)</option>
            </select>
          </div>
        </div>
        {funnelMsg ? (
          <p className="text-xs text-success">{funnelMsg}</p>
        ) : null}
        {funnelErr ? (
          <p className="text-xs text-error">Errore: {funnelErr}</p>
        ) : null}
        <p className="text-xs text-on-surface-variant">
          Richiede L0 completato. Gira in background — ricarica la pagina
          dopo 10-30 min per vedere i candidati.
        </p>
      </div>
    </div>
  );
}
