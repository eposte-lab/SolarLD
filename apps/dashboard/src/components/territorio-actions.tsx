'use client';

/**
 * Client component for the "Rimappa il territorio" button.
 *
 * Triggers POST /v1/territory/map and shows a transient notification
 * with the returned job_id. The actual mapping runs async (5-15 min);
 * the user can leave the page and come back to see updated zone count.
 */

import { useState } from 'react';

import { mapTerritory } from '@/lib/data/territory';

export function TerritorioActions() {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function handleClick() {
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const res = await mapTerritory();
      setMsg(
        `Mappatura avviata (job ${res.job_id.slice(0, 8)}…) — settori: ${
          res.wizard_groups.join(', ') || '—'
        } su ${res.province_codes.join(', ') || '—'}.`,
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'map_failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        className="rounded-full bg-primary px-4 py-2 text-sm font-semibold text-on-primary shadow-ambient-sm transition-colors hover:bg-primary/90 disabled:opacity-50"
      >
        {busy ? 'Avvio in corso…' : 'Rimappa il territorio'}
      </button>
      {msg ? <p className="text-xs text-success">{msg}</p> : null}
      {err ? <p className="text-xs text-error">Errore: {err}</p> : null}
    </div>
  );
}
