'use client';

/**
 * FollowUpDrafter — CTA + Dialog modal for composing a follow-up.
 *
 * Replaces the old collapsible card with a prominent CTA on the lead
 * detail page. Click → modal opens with two modes:
 *
 *   Template  — pre-built Italian copy with variable substitution
 *               ({{nome}}, {{azienda}}, {{kwp}}, {{risparmio}}, etc.)
 *               Three categories: caldo / tiepido / freddo lead.
 *
 *   AI Live   — real-time Claude generation using the lead's full
 *               context (ROI, engagement, campaign history).
 *
 * Both flows produce an editable {subject, body} pair that is then sent
 * via POST /v1/leads/{id}/send-draft. The HTML email is built server
 * side (`_text_to_html(text, tenant=...)`) — operator works on plain
 * text, the API wraps it in the anti-spam HTML shell.
 *
 * The sender (followup_from_email) is shown so the operator knows
 * which inbox the email leaves from. Click-to-edit goes to /settings.
 */

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { AlertTriangle, Mail, Send, Sparkles, X } from 'lucide-react';

import { api, ApiError } from '@/lib/api-client';
import { cn } from '@/lib/utils';
import {
  mergeFollowupTemplates,
  type FollowupTemplate,
  type FollowupTemplateOverrides,
} from '@/lib/followup-templates';

// ---------------------------------------------------------------------------
// Types
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

type Mode = 'template' | 'ai';
type Phase = 'compose' | 'sending' | 'sent' | 'error';

interface Props {
  leadId: string;
  leadName: string;
  /** Recipient email (subjects.decision_maker_email). null = no email on file. */
  recipientEmail: string | null;
  /** Configured sender inbox (tenants.followup_from_email). */
  senderEmail: string | null;
  /** Display name for the sender (tenants.email_from_name / business_name). */
  senderName: string;
  /** Override per-tenant dei template (tenants.followup_templates). */
  tenantTemplates?: FollowupTemplateOverrides | null;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function FollowUpDrafter({
  leadId,
  leadName,
  recipientEmail,
  senderEmail,
  senderName,
  tenantTemplates,
}: Props) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-xl">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Follow-up
          </p>
          <h3 className="mt-1 font-headline text-2xl font-bold tracking-tighter">
            Scrivi al cliente
          </h3>
          <p className="mt-2 text-sm text-on-surface-variant">
            Usa un template precompilato con i dati di {leadName} oppure
            genera una bozza personalizzata con l&apos;AI partendo da ROI,
            engagement e cronologia.
          </p>
          <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-on-surface-variant">
            <Mail size={12} aria-hidden />
            Mittente:{' '}
            {senderEmail ? (
              <span className="font-mono text-on-surface">
                {senderName} &lt;{senderEmail}&gt;
              </span>
            ) : (
              <span className="text-warning">
                non configurato — vai in /settings
              </span>
            )}
          </p>
        </div>
        <button
          onClick={() => setOpen(true)}
          disabled={!recipientEmail}
          className={cn(
            'inline-flex items-center gap-2 rounded-lg px-5 py-3 text-sm font-semibold shadow-ambient-sm transition-colors',
            'bg-primary text-on-primary hover:bg-primary/90',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
          title={
            recipientEmail
              ? 'Apri il modulo follow-up'
              : 'Nessuna email destinatario su questo lead'
          }
        >
          <Send size={14} strokeWidth={2.5} />
          Scrivi follow-up
        </button>
      </div>

      {open && recipientEmail && (
        <FollowUpDialog
          leadId={leadId}
          recipientEmail={recipientEmail}
          senderEmail={senderEmail}
          senderName={senderName}
          tenantTemplates={tenantTemplates}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Dialog
// ---------------------------------------------------------------------------

function FollowUpDialog({
  leadId,
  recipientEmail,
  senderEmail,
  senderName,
  tenantTemplates,
  onClose,
}: {
  leadId: string;
  recipientEmail: string;
  senderEmail: string | null;
  senderName: string;
  tenantTemplates: FollowupTemplateOverrides | null | undefined;
  onClose: () => void;
}) {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>('template');
  const [phase, setPhase] = useState<Phase>('compose');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // I template del tenant (override su tenants.followup_templates)
  // sovrascritti sui default; almeno una voce è sempre presente.
  const templates = mergeFollowupTemplates(tenantTemplates);
  const defaultTemplate = templates[0]!;
  const [activeTemplateId, setActiveTemplateId] = useState<string>(defaultTemplate.id);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiAttempted, setAiAttempted] = useState(false);

  const [subject, setSubject] = useState(defaultTemplate.subject);
  const [body, setBody] = useState(defaultTemplate.body);

  const [showHtmlPreview, setShowHtmlPreview] = useState(false);

  // ESC closes the dialog.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  // Lock body scroll while open.
  useEffect(() => {
    const original = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = original;
    };
  }, []);

  function applyTemplate(id: string) {
    const tpl = templates.find((t) => t.id === id);
    if (!tpl) return;
    setActiveTemplateId(id);
    setSubject(tpl.subject);
    setBody(tpl.body);
  }

  async function generateAI() {
    setAiBusy(true);
    setAiAttempted(true);
    setErrorMsg(null);
    try {
      const draft = await api.post<DraftResponse>(
        `/v1/leads/${leadId}/draft-followup`,
        {},
      );
      setSubject(draft.subject);
      setBody(draft.body);
      setMode('ai');
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError
          ? `Generazione AI fallita: ${err.message}`
          : 'Errore inatteso. Puoi sempre partire da un template precompilato.',
      );
    } finally {
      setAiBusy(false);
    }
  }

  async function send() {
    if (!subject.trim() || !body.trim()) return;
    setPhase('sending');
    setErrorMsg(null);
    try {
      await api.post<SendResponse>(`/v1/leads/${leadId}/send-draft`, {
        subject: subject.trim(),
        body: body.trim(),
      });
      setPhase('sent');
      router.refresh();
      // Auto-close after success notice.
      setTimeout(onClose, 1800);
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError
          ? `Invio fallito: ${err.message}`
          : 'Errore inatteso durante l’invio. Riprova tra qualche minuto.',
      );
      setPhase('error');
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
      role="dialog"
      aria-modal="true"
      aria-labelledby="followup-dialog-title"
    >
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-surface-container-lowest shadow-ambient-lg">
        {/* Header */}
        <div className="flex items-start justify-between gap-4 border-b border-outline-variant/30 px-6 py-4">
          <div>
            <h2
              id="followup-dialog-title"
              className="font-headline text-xl font-bold tracking-tight text-on-surface"
            >
              Scrivi follow-up
            </h2>
            <p className="mt-0.5 text-xs text-on-surface-variant">
              Da{' '}
              <span className="font-mono">
                {senderName} &lt;{senderEmail ?? 'non configurato'}&gt;
              </span>{' '}
              · A <span className="font-mono">{recipientEmail}</span>
            </p>
          </div>
          <button
            onClick={onClose}
            className="-m-2 rounded-md p-2 text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
            aria-label="Chiudi"
          >
            <X size={16} strokeWidth={2.25} aria-hidden />
          </button>
        </div>

        {/* Mode tabs */}
        <div className="flex gap-1.5 border-b border-outline-variant/30 px-6 py-2">
          <ModeTab
            active={mode === 'template'}
            onClick={() => setMode('template')}
            icon={<Mail size={12} />}
            label="Template"
          />
          <ModeTab
            active={mode === 'ai'}
            onClick={() => {
              setMode('ai');
              if (!aiAttempted) void generateAI();
            }}
            icon={<Sparkles size={12} />}
            label={aiBusy ? 'Generazione…' : 'AI Live'}
          />
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {phase === 'sent' ? (
            <div className="flex items-start gap-3 rounded-lg bg-primary-container/40 px-4 py-4 text-sm text-on-primary-container">
              <span aria-hidden className="mt-0.5 text-lg leading-none">
                ✓
              </span>
              <div>
                <p className="font-semibold">Email inviata</p>
                <p className="mt-0.5 text-on-primary-container/80">
                  La trovi nella sequenza campagne. La finestra si chiude tra
                  un istante.
                </p>
              </div>
            </div>
          ) : (
            <>
              {errorMsg && (
                <div className="mb-3 flex items-start gap-3 rounded-lg bg-error-container/40 px-4 py-3 text-sm text-on-error-container">
                  <AlertTriangle
                    size={14}
                    strokeWidth={2.25}
                    className="mt-0.5 shrink-0"
                  />
                  <p>{errorMsg}</p>
                </div>
              )}

              {mode === 'template' && (
                <TemplatePicker
                  templates={templates}
                  active={activeTemplateId}
                  onSelect={applyTemplate}
                />
              )}

              {mode === 'ai' && aiBusy && (
                <div className="mb-3 flex items-center gap-3 rounded-lg bg-surface-container-low px-4 py-3 text-sm text-on-surface-variant">
                  <Spinner />
                  L&apos;AI sta analizzando ROI, engagement e cronologia
                  email…
                </div>
              )}

              {mode === 'ai' && !aiBusy && aiAttempted && (
                <div className="mb-3 flex items-center justify-between rounded-lg bg-surface-container-low px-4 py-3 text-xs text-on-surface-variant">
                  <span>Bozza generata sulla base del contesto del lead.</span>
                  <button
                    onClick={generateAI}
                    className="font-semibold text-primary hover:underline"
                  >
                    Rigenera
                  </button>
                </div>
              )}

              {/* Subject + body editor */}
              <div className="space-y-3">
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
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <label className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                      Corpo email
                    </label>
                    <button
                      onClick={() => setShowHtmlPreview((v) => !v)}
                      type="button"
                      className="text-[10px] font-semibold text-primary hover:underline"
                    >
                      {showHtmlPreview ? 'Nascondi anteprima' : 'Anteprima HTML'}
                    </button>
                  </div>
                  <textarea
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                    disabled={phase === 'sending'}
                    rows={12}
                    maxLength={8000}
                    className={cn(
                      'w-full resize-y rounded-lg border border-outline-variant/40 bg-surface-container-lowest',
                      'px-3 py-2 text-sm leading-relaxed text-on-surface placeholder-on-surface-variant/60',
                      'focus:border-primary/60 focus:outline-none',
                      'disabled:opacity-60',
                    )}
                  />
                  <p className="mt-1 text-right text-[10px] text-on-surface-variant">
                    {body.length} / 8000
                    {mode === 'template' && ' · sostituzioni {{...}} risolte all’invio'}
                  </p>
                </div>

                {showHtmlPreview && (
                  <HtmlPreview
                    body={body}
                    senderName={senderName}
                    recipientEmail={recipientEmail}
                  />
                )}
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        {phase !== 'sent' && (
          <div className="flex items-center justify-between gap-3 border-t border-outline-variant/30 bg-surface-container-low px-6 py-4">
            <p className="text-[11px] text-on-surface-variant">
              Email inviata in HTML professionale anti-spam con fallback in
              testo semplice.
            </p>
            <div className="flex items-center gap-3">
              <button
                onClick={onClose}
                disabled={phase === 'sending'}
                className="text-sm text-on-surface-variant hover:text-on-surface hover:underline disabled:opacity-50"
              >
                Annulla
              </button>
              <button
                onClick={send}
                disabled={
                  phase === 'sending' ||
                  !subject.trim() ||
                  !body.trim() ||
                  !senderEmail
                }
                className={cn(
                  'inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold text-on-primary shadow-ambient-sm transition-colors',
                  'bg-primary hover:bg-primary/90',
                  'disabled:cursor-not-allowed disabled:opacity-50',
                )}
              >
                {phase === 'sending' ? (
                  <>
                    <Spinner className="text-on-primary" />
                    Invio…
                  </>
                ) : (
                  <>
                    <Send size={14} strokeWidth={2.5} />
                    Invia ora
                  </>
                )}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ModeTab({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold transition-colors',
        active
          ? 'bg-primary-container text-on-primary-container'
          : 'text-on-surface-variant hover:bg-surface-container hover:text-on-surface',
      )}
    >
      {icon}
      {label}
    </button>
  );
}

function TemplatePicker({
  templates,
  active,
  onSelect,
}: {
  templates: FollowupTemplate[];
  active: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="mb-4 grid gap-2 sm:grid-cols-2">
      {templates.map((tpl) => (
        <button
          key={tpl.id}
          onClick={() => onSelect(tpl.id)}
          className={cn(
            'rounded-lg border px-3 py-2.5 text-left text-xs transition-colors',
            active === tpl.id
              ? 'border-primary bg-primary-container/30 text-on-surface'
              : 'border-outline-variant/40 bg-surface-container-lowest text-on-surface-variant hover:border-primary/40 hover:bg-surface-container',
          )}
        >
          <div className="font-semibold text-on-surface">{tpl.label}</div>
          <p className="mt-0.5 leading-snug">{tpl.description}</p>
        </button>
      ))}
    </div>
  );
}

/**
 * HTML preview — replicates server-side `_text_to_html()` so what the
 * operator sees here matches what lands in the recipient inbox.
 */
function HtmlPreview({
  body,
  senderName,
  recipientEmail: _recipientEmail,
}: {
  body: string;
  senderName: string;
  recipientEmail: string;
}) {
  // Simple paragraph rendering — NOT identical to the server but close
  // enough to give the operator an idea of the layout. The server still
  // owns the source of truth.
  const paragraphs = body
    .split(/\n\n+/)
    .map((p) => p.trim())
    .filter(Boolean);

  return (
    <div className="rounded-xl border border-outline-variant/40 bg-[#f9fafb] p-6">
      <p className="mb-3 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
        Anteprima
      </p>
      <div className="mx-auto max-w-[600px] rounded-xl bg-white p-7 text-sm leading-relaxed text-[#1f2937] shadow-ambient-sm">
        {paragraphs.map((p, idx) => (
          <p key={idx} className="mb-3 last:mb-0 whitespace-pre-line">
            {p}
          </p>
        ))}
        <hr className="my-4 border-t border-[#e5e7eb]" />
        <p className="text-[12px] leading-snug text-[#6b7280]">
          <strong className="text-[#374151]">{senderName}</strong>
        </p>
      </div>
    </div>
  );
}

function Spinner({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="14"
      height="14"
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
