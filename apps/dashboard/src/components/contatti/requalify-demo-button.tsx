'use client';

/**
 * RequalifyDemoButton — shown on /contatti when the demo tenant has 0
 * accepted candidates (all rejected at L4 Solar gate).
 *
 * Calls POST /v1/leads/requalify-demo-l4 which re-evaluates existing
 * rejected candidates against relaxed demo thresholds (30 m², 10 kWp,
 * 700 h sunshine) and promotes newly-qualifying ones to the pipeline.
 */

import { useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { apiFetch } from '@/lib/api-client';

interface RequalifyResult {
  ok: boolean;
  is_demo: boolean;
  total_rejected: number;
  newly_accepted: number;
  skipped_no_solar_data: number;
  dry_run: boolean;
  thresholds?: {
    min_area_m2: number;
    min_kw_installable: number;
    min_sunshine_hours: number;
  };
}

export function RequalifyDemoButton() {
  const [status, setStatus] = useState<'idle' | 'loading' | 'done' | 'error'>('idle');
  const [result, setResult] = useState<RequalifyResult | null>(null);
  const [errorMsg, setErrorMsg] = useState('');

  const run = async () => {
    setStatus('loading');
    setResult(null);
    setErrorMsg('');
    try {
      const data = await apiFetch<RequalifyResult>(
        '/v1/leads/requalify-demo-l4',
        { method: 'POST' },
      );
      setResult(data);
      setStatus('done');
      // Reload the page after a short delay so the KPI strip refreshes.
      if (data.newly_accepted > 0) {
        setTimeout(() => window.location.reload(), 2000);
      }
    } catch (err: unknown) {
      setStatus('error');
      setErrorMsg(err instanceof Error ? err.message : 'Errore sconosciuto');
    }
  };

  if (status === 'done' && result) {
    return (
      <div className="rounded-lg bg-secondary-container/30 px-4 py-3 text-sm text-on-secondary-container">
        {result.newly_accepted > 0 ? (
          <p>
            ✓ <strong>{result.newly_accepted}</strong> contatti riqualificati con soglie demo
            {result.newly_accepted > 0 && ' — ricarico la pagina…'}
          </p>
        ) : (
          <p className="text-on-surface-variant">
            Nessun contatto nuovo: tutti i {result.total_rejected} candidati rifiutati non
            raggiungono le soglie demo (area ≥ {result.thresholds?.min_area_m2} m²,
            {' '}{result.thresholds?.min_kw_installable} kWp,
            {' '}{result.thresholds?.min_sunshine_hours} h sole/anno).
          </p>
        )}
      </div>
    );
  }

  if (status === 'error') {
    return (
      <p className="text-sm text-error">
        Errore nella riqualifica: {errorMsg}
      </p>
    );
  }

  return (
    <button
      onClick={run}
      disabled={status === 'loading'}
      className="inline-flex items-center gap-2 rounded-lg bg-tertiary px-4 py-2 text-sm font-semibold text-on-tertiary transition hover:opacity-90 disabled:opacity-50"
    >
      <RefreshCw size={14} className={status === 'loading' ? 'animate-spin' : ''} />
      {status === 'loading' ? 'Riqualifica in corso…' : 'Riqualifica con soglie demo'}
    </button>
  );
}
