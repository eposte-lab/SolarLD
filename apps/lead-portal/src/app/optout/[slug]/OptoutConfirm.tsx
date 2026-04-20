'use client';

import { useState } from 'react';
import { API_URL } from '@/lib/api';

type Status = 'idle' | 'submitting' | 'success' | 'error';

export function OptoutConfirm({ slug }: { slug: string }) {
  const [status, setStatus] = useState<Status>('idle');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  async function handleConfirm() {
    if (status === 'submitting') return;
    setStatus('submitting');
    setErrorMsg(null);
    try {
      const res = await fetch(
        `${API_URL}/v1/public/lead/${encodeURIComponent(slug)}/optout`,
        { method: 'POST' },
      );
      // 404 (slug not found) is treated as already-done from the user's POV.
      if (!res.ok && res.status !== 404) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
      }
      setStatus('success');
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Errore inatteso.');
      setStatus('error');
    }
  }

  if (status === 'success') {
    return (
      <div className="mt-4 rounded-md bg-green-50 p-3 text-sm text-green-700">
        Richiesta registrata. Non riceverete più comunicazioni.
      </div>
    );
  }

  return (
    <div className="mt-4 space-y-2">
      <button
        onClick={handleConfirm}
        disabled={status === 'submitting'}
        className="w-full rounded-md bg-slate-900 px-4 py-2 text-sm font-semibold text-white shadow disabled:opacity-60"
      >
        {status === 'submitting' ? 'In corso…' : 'Conferma la disiscrizione'}
      </button>
      {errorMsg ? <p className="text-xs text-red-600">{errorMsg}</p> : null}
    </div>
  );
}
