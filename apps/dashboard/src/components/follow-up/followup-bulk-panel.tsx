'use client';

/**
 * FollowupBulkPanel — operator-driven bulk AI follow-up.
 *
 * Flow:
 *   1. Operator pastes or types lead IDs (one per line) OR clicks
 *      "Seleziona tutti i lead attivi" to auto-fill from the API.
 *   2. Clicks "Genera bozze AI" — calls POST /v1/followup/bulk-draft
 *      with send_immediately=false. Results appear in a scrollable list.
 *   3. Per-draft: "Invia" (calls POST /leads/{id}/send-draft inline) or
 *      "Apri lead" to review on the full detail page.
 *   4. Or: "Genera e invia tutto" — send_immediately=true, skips review.
 */

import { useState } from 'react';
import Link from 'next/link';
import { AlertTriangle } from 'lucide-react';

import { api, ApiError } from '@/lib/api-client';
import { cn } from '@/lib/utils';

interface DraftResult {
  lead_id: string;
  ok: boolean;
  subject: string | null;
  body: string | null;
  error: string | null;
}

type DraftState = DraftResult & { sendStatus: 'idle' | 'sending' | 'sent' | 'error'; sendError?: string };

interface Props {
  tenantId: string;
}

export function FollowupBulkPanel({ tenantId: _tenantId }: Props) {
  const [leadIdsText, setLeadIdsText] = useState('');
  const [generatingStatus, setGeneratingStatus] = useState<'idle' | 'loading' | 'done' | 'error'>('idle');
  const [drafts, setDrafts] = useState<DraftState[]>([]);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [loadingLeads, setLoadingLeads] = useState(false);

  function parseLeadIds(): string[] {
    return leadIdsText
      .split(/[\n,;]+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
  }

  async function loadActiveLeads() {
    setLoadingLeads(true);
    try {
      const res = await api.get<{ rows: Array<{ id: string }> }>('/v1/leads?pageSize=50&status=sent');
      const ids = (res.rows ?? []).map((r) => r.id).join('\n');
      setLeadIdsText(ids);
    } catch {
      // fallback: ignore, let operator type manually
    } finally {
      setLoadingLeads(false);
    }
  }

  async function generateDrafts(sendImmediately = false) {
    const ids = parseLeadIds();
    if (ids.length === 0) return;
    if (ids.length > 50) {
      setGlobalError('Massimo 50 lead per richiesta. Riduci la lista.');
      return;
    }
    setGeneratingStatus('loading');
    setGlobalError(null);
    setDrafts([]);
    try {
      const results = await api.post<DraftResult[]>('/v1/followup/bulk-draft', {
        lead_ids: ids,
        send_immediately: sendImmediately,
      });
      setDrafts(results.map((r) => ({ ...r, sendStatus: sendImmediately && r.ok ? 'sent' : 'idle' })));
      setGeneratingStatus('done');
    } catch (err) {
      // ApiError.message is already sanitized Italian copy.
      setGlobalError(
        err instanceof ApiError
          ? err.message
          : 'Errore inatteso durante la generazione. Riprova tra qualche minuto.',
      );
      setGeneratingStatus('error');
    }
  }

  async function sendDraft(idx: number) {
    const draft = drafts[idx];
    if (!draft || !draft.ok || !draft.subject || !draft.body) return;
    setDrafts((prev) =>
      prev.map((d, i) => (i === idx ? { ...d, sendStatus: 'sending' } : d)),
    );
    try {
      await api.post(`/v1/leads/${draft.lead_id}/send-draft`, {
        subject: draft.subject,
        body: draft.body,
      });
      setDrafts((prev) =>
        prev.map((d, i) => (i === idx ? { ...d, sendStatus: 'sent' } : d)),
      );
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.message : 'Errore durante l\'invio.';
      setDrafts((prev) =>
        prev.map((d, i) =>
          i === idx ? { ...d, sendStatus: 'error', sendError: msg } : d,
        ),
      );
    }
  }

  const leadIds = parseLeadIds();

  return (
    <div className="space-y-4">
      {/* Lead ID input */}
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <label className="text-sm font-medium text-on-surface">
            ID lead (uno per riga, max 50)
          </label>
          <button
            onClick={loadActiveLeads}
            disabled={loadingLeads}
            className="text-xs text-primary hover:underline disabled:opacity-50"
          >
            {loadingLeads ? 'Carico…' : 'Seleziona lead attivi'}
          </button>
        </div>
        <textarea
          value={leadIdsText}
          onChange={(e) => setLeadIdsText(e.target.value)}
          rows={4}
          placeholder="Incolla gli UUID dei lead qui, uno per riga&#10;oppure usa il link sopra per caricare i lead attivi"
          className={cn(
            'w-full resize-y rounded-lg border border-outline-variant/40 bg-surface-container-lowest',
            'px-3 py-2 font-mono text-xs text-on-surface placeholder-on-surface-variant/50',
            'focus:border-primary/60 focus:outline-none',
          )}
        />
        {leadIds.length > 0 && (
          <p className="mt-1 text-xs text-on-surface-variant">
            {leadIds.length} lead selezionati
          </p>
        )}
      </div>

      {globalError && (
        <div className="flex items-start gap-2 rounded-lg bg-error-container/40 px-3 py-2 text-sm text-on-error-container">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
          <p>{globalError}</p>
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={() => generateDrafts(false)}
          disabled={leadIds.length === 0 || generatingStatus === 'loading'}
          className={cn(
            'inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold',
            'bg-surface-container text-on-surface shadow-ambient-sm transition-colors',
            'hover:bg-surface-container-high',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
        >
          {generatingStatus === 'loading' ? (
            <>
              <SpinnerIcon />
              Generazione bozze…
            </>
          ) : (
            <>
              <SparkleIcon />
              Genera bozze AI
            </>
          )}
        </button>

        <button
          onClick={() => generateDrafts(true)}
          disabled={leadIds.length === 0 || generatingStatus === 'loading'}
          className={cn(
            'inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold',
            'bg-primary text-on-primary shadow-ambient-sm transition-colors',
            'hover:bg-primary/90',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
        >
          Genera e invia tutto
        </button>
      </div>

      {/* Draft results */}
      {drafts.length > 0 && (
        <div className="mt-2 space-y-3">
          <p className="text-xs text-on-surface-variant">
            {drafts.filter((d) => d.ok).length} bozze generate,{' '}
            {drafts.filter((d) => !d.ok).length} non riuscite
          </p>
          <div className="max-h-[480px] overflow-y-auto rounded-xl border border-outline-variant/30">
            {drafts.map((draft, idx) => (
              <DraftRow
                key={draft.lead_id}
                draft={draft}
                idx={idx}
                onSend={() => sendDraft(idx)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function DraftRow({
  draft,
  idx,
  onSend,
}: {
  draft: DraftState;
  idx: number;
  onSend: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      className={cn(
        'border-b border-outline-variant/20 px-4 py-3 last:border-0',
        !draft.ok && 'bg-error-container/10',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-xs text-on-surface-variant">
            {draft.lead_id}
          </p>
          {draft.ok ? (
            <p className="mt-0.5 truncate text-sm font-medium text-on-surface">
              {draft.subject ?? '(no subject)'}
            </p>
          ) : (
            <p className="mt-0.5 text-xs text-error">{draft.error}</p>
          )}
        </div>

        {draft.ok && (
          <div className="flex shrink-0 items-center gap-2">
            {draft.sendStatus === 'sent' ? (
              <span className="text-xs text-primary">Inviato</span>
            ) : draft.sendStatus === 'error' ? (
              <span className="text-xs text-error" title={draft.sendError}>
                Errore invio
              </span>
            ) : (
              <button
                onClick={onSend}
                disabled={draft.sendStatus === 'sending'}
                className={cn(
                  'rounded-md px-3 py-1 text-xs font-semibold',
                  'bg-primary text-on-primary hover:bg-primary/90',
                  'disabled:opacity-50',
                )}
              >
                {draft.sendStatus === 'sending' ? 'Invio…' : 'Invia'}
              </button>
            )}
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-xs text-on-surface-variant hover:text-on-surface"
            >
              {expanded ? 'Chiudi' : 'Anteprima'}
            </button>
            <Link
              href={`/leads/${draft.lead_id}`}
              className="text-xs text-on-surface-variant hover:text-primary hover:underline"
            >
              Apri
            </Link>
          </div>
        )}
      </div>

      {expanded && draft.ok && draft.body && (
        <pre className="mt-3 whitespace-pre-wrap rounded-lg bg-surface-container-low p-3 font-sans text-xs text-on-surface leading-relaxed">
          {draft.body}
        </pre>
      )}
    </div>
  );
}

function SparkleIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3l1.9 5.6L19 10.7l-5.1.9L12 17l-1.9-5.4L5 10.7l5.1-.9z" />
      <path d="M5 3v4M19 17v4M3 5h4M17 19h4" />
    </svg>
  );
}

function SpinnerIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
      className="animate-spin">
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  );
}
