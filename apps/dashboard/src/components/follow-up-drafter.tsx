'use client';

/**
 * Follow-up drafter — Part B.9.
 *
 * Three-state UI:
 *   idle      → "Genera follow-up AI" button
 *   drafting  → spinner + "Generazione in corso…"
 *   drafted   → editable subject + textarea with "Invia" / "Scarta"
 *   sending   → spinner on Invia button
 *   sent      → success banner
 *   error     → red flash banner, back to idle/drafted state
 *
 * Design:
 *   - The generate call hits POST /v1/leads/{id}/draft-followup.
 *   - The send call hits POST /v1/leads/{id}/send-draft.
 *   - Both use `api` from lib/api-client.ts which auto-attaches the JWT.
 *   - After send the parent page refreshes via router.refresh() so the
 *     campaign sequence section picks up the new row.
 *   - Tier-gated server-side (Pro+). The component is mounted only when
 *     the tier allows it — no client-side gate needed.
 */

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { AlertTriangle } from 'lucide-react';

import { api, ApiError } from '@/lib/api-client';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Types (mirror the FastAPI response shapes)
// ---------------------------------------------------------------------------

interface DraftResponse {
  lead_id: string;
  subject: string;
  body: string;
}

interface SendResponse {
  ok: boolean;
  campaign_id: string;
  message_id: string | null;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function FollowUpDrafter({ leadId }: { leadId: string }) {
  const router = useRouter();

  const [phase, setPhase] = useState<
    'idle' | 'drafting' | 'drafted' | 'sending' | 'sent' | 'error'
  >('idle');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // -------------------------------------------------------------------------

  async function generateDraft() {
    setPhase('drafting');
    setErrorMsg(null);
    try {
      const draft = await api.post<DraftResponse>(
        `/v1/leads/${leadId}/draft-followup`,
        {},
      );
      setSubject(draft.subject);
      setBody(draft.body);
      setPhase('drafted');
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError
          ? `Generazione fallita (${err.status}): ${err.message}`
          : 'Errore inatteso durante la generazione.',
      );
      setPhase('error');
    }
  }

  async function sendDraft() {
    if (!subject.trim() || !body.trim()) return;
    setPhase('sending');
    setErrorMsg(null);
    try {
      await api.post<SendResponse>(`/v1/leads/${leadId}/send-draft`, {
        subject: subject.trim(),
        body: body.trim(),
      });
      setPhase('sent');
      // Refresh server data so the campaign sequence updates
      router.refresh();
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError
          ? `Invio fallito (${err.status}): ${err.message}`
          : "Errore inatteso durante l\u2019invio.",
      );
      setPhase('drafted'); // go back to editable state, not to idle
    }
  }

  function discard() {
    setPhase('idle');
    setSubject('');
    setBody('');
    setErrorMsg(null);
  }

  // -------------------------------------------------------------------------
  // Render

  if (phase === 'sent') {
    return (
      <div className="flex items-start gap-3 rounded-lg bg-primary-container/40 px-4 py-3 text-sm text-on-primary-container">
        <span aria-hidden className="mt-0.5 text-lg leading-none">✓</span>
        <div>
          <p className="font-semibold">Follow-up inviato</p>
          <p className="mt-0.5 text-on-primary-container/80">
            La mail è in viaggio. Trovi il record nella sequenza campagne qui sopra.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Error banner */}
      {errorMsg && phase === 'error' && (
        <div className="flex items-start gap-3 rounded-lg bg-error-container/40 px-4 py-3 text-sm text-on-error-container">
          <AlertTriangle size={14} strokeWidth={2.25} aria-hidden className="mt-0.5 shrink-0" />
          <p>{errorMsg}</p>
          <button
            onClick={() => { setPhase('idle'); setErrorMsg(null); }}
            className="ml-auto shrink-0 font-semibold underline hover:no-underline"
          >
            Riprova
          </button>
        </div>
      )}

      {/* Inline send error (stays on drafted state) */}
      {errorMsg && phase === 'drafted' && (
        <div className="flex items-start gap-3 rounded-lg bg-error-container/40 px-4 py-3 text-sm text-on-error-container">
          <AlertTriangle size={14} strokeWidth={2.25} aria-hidden className="mt-0.5 shrink-0" />
          <p>{errorMsg}</p>
        </div>
      )}

      {/* Idle state */}
      {phase === 'idle' && (
        <button
          onClick={generateDraft}
          className="inline-flex items-center gap-2 rounded-lg bg-surface-container px-4 py-2.5 text-sm font-semibold text-on-surface shadow-ambient-sm transition-colors hover:bg-surface-container-high"
        >
          <SparkleIcon />
          Genera follow-up AI
        </button>
      )}

      {/* Generating */}
      {phase === 'drafting' && (
        <div className="flex items-center gap-3 rounded-lg bg-surface-container-low px-4 py-3 text-sm text-on-surface-variant">
          <SpinnerIcon />
          Il sistema sta analizzando il contesto del lead e scrivendo la bozza…
        </div>
      )}

      {/* Draft editor */}
      {(phase === 'drafted' || phase === 'sending') && (
        <div className="space-y-3">
          {/* Context notice */}
          <p className="text-xs text-on-surface-variant">
            Bozza generata automaticamente sulla base di ROI, engagement e cronologia delle email inviate.
            Modifica liberamente prima di inviare.
          </p>

          {/* Subject */}
          <div>
            <label className="mb-1 block text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Oggetto
            </label>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              disabled={phase === 'sending'}
              maxLength={300}
              className={cn(
                'w-full rounded-lg border border-outline-variant/40 bg-surface-container-lowest',
                'px-3 py-2 text-sm text-on-surface placeholder-on-surface-variant/60',
                'focus:border-primary/60 focus:outline-none',
                'disabled:opacity-60',
              )}
            />
          </div>

          {/* Body */}
          <div>
            <label className="mb-1 block text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Corpo email (testo)
            </label>
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              disabled={phase === 'sending'}
              rows={12}
              maxLength={8000}
              className={cn(
                'w-full resize-y rounded-lg border border-outline-variant/40 bg-surface-container-lowest',
                'px-3 py-2 font-mono text-sm text-on-surface placeholder-on-surface-variant/60',
                'focus:border-primary/60 focus:outline-none',
                'disabled:opacity-60',
              )}
            />
            <p className="mt-1 text-right text-[10px] text-on-surface-variant">
              {body.length} / 8000 caratteri
            </p>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3">
            <button
              onClick={sendDraft}
              disabled={phase === 'sending' || !subject.trim() || !body.trim()}
              className={cn(
                'inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold text-on-primary shadow-ambient-sm transition-colors',
                'bg-primary hover:bg-primary/90',
                'disabled:cursor-not-allowed disabled:opacity-50',
              )}
            >
              {phase === 'sending' ? (
                <>
                  <SpinnerIcon className="text-on-primary" />
                  Invio in corso…
                </>
              ) : (
                'Invia follow-up'
              )}
            </button>

            <button
              onClick={generateDraft}
              disabled={phase === 'sending'}
              className="inline-flex items-center gap-2 rounded-lg bg-surface-container px-4 py-2.5 text-sm font-semibold text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
            >
              <SparkleIcon />
              Rigenera
            </button>

            <button
              onClick={discard}
              disabled={phase === 'sending'}
              className="ml-auto text-sm text-on-surface-variant hover:text-on-surface hover:underline disabled:opacity-50"
            >
              Scarta
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Micro icons — inline SVGs, no library dep
// ---------------------------------------------------------------------------

function SparkleIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      <path d="M12 3l1.9 5.6L19 10.7l-5.1.9L12 17l-1.9-5.4L5 10.7l5.1-.9z" />
      <path d="M5 3v4M19 17v4M3 5h4M17 19h4" />
    </svg>
  );
}

function SpinnerIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="16"
      height="16"
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
