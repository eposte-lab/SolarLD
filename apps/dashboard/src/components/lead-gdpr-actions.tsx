'use client';

/**
 * GDPR actions for a single lead — Part B.11.
 *
 * Two operations:
 *   1. **Esporta JSON** — fetches the full lead detail from the API and
 *      triggers a browser download. Useful when a data subject requests
 *      a copy of their data under GDPR Art. 15.
 *   2. **Elimina permanentemente** — hard-deletes the lead (and all
 *      cascaded child rows) via DELETE /v1/leads/{id}. Irreversible.
 *      Used to comply with a right-to-erasure request (GDPR Art. 17).
 *
 * Both operations are guarded by a `window.confirm()` before execution.
 * After a successful delete the component calls `onDeleted()` so the
 * parent page can redirect to /leads.
 */

import { useState } from 'react';

import { api, ApiError } from '@/lib/api-client';
import { cn } from '@/lib/utils';

interface Props {
  leadId: string;
  leadName: string;
  onDeleted: () => void;
}

type Phase = 'idle' | 'exporting' | 'deleting' | 'deleted' | 'error';

export function LeadGdprActions({ leadId, leadName, onDeleted }: Props) {
  const [phase, setPhase] = useState<Phase>('idle');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // -------------------------------------------------------------------------
  // Export JSON

  async function handleExport() {
    const ok = window.confirm(
      `Scaricare tutti i dati di "${leadName}" in formato JSON?\n\n` +
        'Il file conterrà tutte le informazioni personali associate a questo lead.',
    );
    if (!ok) return;

    setPhase('exporting');
    setErrorMsg(null);
    try {
      const data = await api.get(`/v1/leads/${leadId}`);
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: 'application/json',
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `lead-${leadId}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setPhase('idle');
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError
          ? `Export fallito (${err.status}): ${err.message}`
          : 'Errore inatteso durante l\u2019export.',
      );
      setPhase('error');
    }
  }

  // -------------------------------------------------------------------------
  // Delete

  async function handleDelete() {
    const firstConfirm = window.confirm(
      `Sei sicuro di voler eliminare DEFINITIVAMENTE il lead "${leadName}"?\n\n` +
        'Questa azione è irreversibile. Saranno eliminati: anagrafica, tetto, campagne, eventi, conversioni.',
    );
    if (!firstConfirm) return;

    const secondConfirm = window.confirm(
      `ULTIMA CONFERMA: eliminare permanentemente "${leadName}"?`,
    );
    if (!secondConfirm) return;

    setPhase('deleting');
    setErrorMsg(null);
    try {
      await api.delete(`/v1/leads/${leadId}`);
      setPhase('deleted');
      onDeleted();
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError
          ? `Eliminazione fallita (${err.status}): ${err.message}`
          : 'Errore inatteso durante l\u2019eliminazione.',
      );
      setPhase('error');
    }
  }

  // -------------------------------------------------------------------------

  if (phase === 'deleted') {
    return (
      <p className="text-sm text-on-surface-variant">
        Lead eliminato. Reindirizzamento…
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {errorMsg && (
        <div className="flex items-start gap-3 rounded-lg bg-error-container/40 px-4 py-3 text-sm text-on-error-container">
          <span aria-hidden className="mt-0.5">⚠</span>
          <p className="flex-1">{errorMsg}</p>
          <button
            onClick={() => { setPhase('idle'); setErrorMsg(null); }}
            className="shrink-0 font-semibold underline hover:no-underline"
          >
            OK
          </button>
        </div>
      )}

      <div className="flex flex-wrap gap-3">
        {/* Export */}
        <button
          onClick={handleExport}
          disabled={phase === 'exporting' || phase === 'deleting'}
          className={cn(
            'inline-flex items-center gap-2 rounded-lg border border-outline-variant/60',
            'bg-surface-container px-4 py-2.5 text-sm font-semibold text-on-surface',
            'transition-colors hover:bg-surface-container-high',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
        >
          {phase === 'exporting' ? (
            <SmallSpinner />
          ) : (
            <DownloadIcon />
          )}
          {phase === 'exporting' ? 'Esportazione…' : 'Esporta dati JSON'}
        </button>

        {/* Delete */}
        <button
          onClick={handleDelete}
          disabled={phase === 'exporting' || phase === 'deleting'}
          className={cn(
            'inline-flex items-center gap-2 rounded-lg border border-error/40',
            'bg-error-container/20 px-4 py-2.5 text-sm font-semibold text-error',
            'transition-colors hover:bg-error-container/40',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
        >
          {phase === 'deleting' ? (
            <SmallSpinner className="text-error" />
          ) : (
            <TrashIcon />
          )}
          {phase === 'deleting' ? 'Eliminazione…' : 'Elimina permanentemente'}
        </button>
      </div>

      <p className="text-xs text-on-surface-variant">
        L&apos;eliminazione è irreversibile e copre anagrafica, tetto, campagne,
        eventi e tutti i dati correlati. L&apos;azione viene registrata nel log di
        audit per conformità GDPR. L&apos;esportazione JSON include tutti i dati
        personali associati al lead.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Micro icons
// ---------------------------------------------------------------------------

function DownloadIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6M14 11v6" />
      <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
  );
}

function SmallSpinner({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={cn('animate-spin', className)}
    >
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  );
}
