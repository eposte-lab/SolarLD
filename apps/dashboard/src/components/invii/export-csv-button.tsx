'use client';

import { useState } from 'react';
import { Download, Loader2 } from 'lucide-react';

import { API_URL } from '@/lib/api-client';
import { createBrowserClient } from '@/lib/supabase/client';

/**
 * Downloads the full outreach-sends CSV (`GET /v1/outreach-sends/export.csv`).
 *
 * The endpoint requires the Supabase JWT, which a plain `<a download>` can't
 * attach — so we fetch with the bearer header, turn the response into a Blob,
 * and click a synthetic link. Exports EVERY send (server-side, un-paginated),
 * not just the rows currently on screen.
 */
export function ExportCsvButton() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const supabase = createBrowserClient();
      const {
        data: { session },
      } = await supabase.auth.getSession();
      const token = session?.access_token;
      const res = await fetch(`${API_URL}/v1/outreach-sends/export.csv`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) throw new Error(`Export non riuscito (codice ${res.status}).`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `outreach_${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Errore durante l’export.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={() => void run()}
        disabled={busy}
        className="inline-flex items-center gap-2 rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-4 py-2 text-sm font-semibold text-on-surface transition-opacity hover:opacity-90 disabled:opacity-50"
      >
        {busy ? (
          <Loader2 size={15} strokeWidth={2.25} aria-hidden className="animate-spin" />
        ) : (
          <Download size={15} strokeWidth={2.25} aria-hidden />
        )}
        Esporta CSV
      </button>
      {error ? <span className="text-xs text-error">{error}</span> : null}
    </div>
  );
}
