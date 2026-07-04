'use client';

/**
 * Dossier survey widget — an engaging "one question at a time" quiz that
 * replaces a flat contact form. It auto-triggers after the visitor has had
 * time to see the page/video, asks a couple of low-friction, curious
 * questions ("just personalising your analysis"), and ends by asking the
 * phone number. A self-provided phone is the HOTTEST contact we can get.
 *
 * Shows ONCE per session, never after a conversion. Shares the exit-intent
 * session flags so the two popups never both fire.
 */

import { useEffect, useRef, useState, type FormEvent } from 'react';

import { submitSurvey, trackSurveyStep } from '@/lib/survey';
import { postPortalEvent } from '@/lib/tracking';

type Status = 'idle' | 'submitting' | 'success' | 'error';

const SHOWN_KEY = 'solarLead.survey.shown';
// Reuse the exit-intent flags so only ONE popup ever surfaces per session.
const EXIT_SHOWN_KEY = 'solarLead.exitIntent.shown';
const EXIT_CLOSED_KEY = 'solarLead.exitIntent.closed';

// The two curious questions. `id` becomes the answer key sent to the backend.
const QUESTIONS: ReadonlyArray<{ id: string; label: string; options: readonly string[] }> = [
  {
    id: 'interesse',
    label: 'Cosa vi interesserebbe di più?',
    options: ['Ridurre la bolletta', 'Indipendenza energetica', 'Incentivi e detrazioni', 'Sostenibilità'],
  },
  {
    id: 'spesa_mensile',
    label: 'Quanto spendete di energia al mese, all’incirca?',
    options: ['Meno di 1.000€', '1.000–5.000€', 'Oltre 5.000€'],
  },
];
const TOTAL_STEPS = QUESTIONS.length + 1; // + the phone step

function flag(key: string): boolean {
  try {
    return Boolean(window.sessionStorage.getItem(key));
  } catch {
    return false;
  }
}

function setFlag(key: string): void {
  try {
    window.sessionStorage.setItem(key, '1');
  } catch {
    /* private-mode Safari throws — the in-memory ref still guards */
  }
}

export function SurveyModal({
  slug,
  brandColor,
  accentColor,
  tenantName,
  privacyPolicyUrl,
  defaultPhone,
  alreadyConverted,
  /** Delay before the quiz auto-surfaces — the caller passes roughly
   *  (video + motion-graphic duration) + a buffer so it fires after the
   *  visitor has seen the content. Defaults to 25s. */
  triggerDelayMs = 25_000,
}: {
  slug: string;
  brandColor: string;
  accentColor?: string;
  tenantName: string;
  privacyPolicyUrl?: string | null;
  defaultPhone?: string | null;
  alreadyConverted: boolean;
  triggerDelayMs?: number;
}) {
  const accent = accentColor || brandColor;
  const [open, setOpen] = useState(false);
  const [stepIdx, setStepIdx] = useState(0); // 0..QUESTIONS.length (last = phone)
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<Status>('idle');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const shownRef = useRef(false);

  // ---- Auto-trigger after the viewing delay (once per session) ----
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (alreadyConverted) return;
    if (flag(SHOWN_KEY) || flag(EXIT_SHOWN_KEY) || flag(EXIT_CLOSED_KEY)) return;

    const t = window.setTimeout(() => {
      if (shownRef.current) return;
      // The exit-intent may have fired during the delay — never double-popup.
      if (flag(EXIT_SHOWN_KEY) || flag(EXIT_CLOSED_KEY)) return;
      shownRef.current = true;
      setFlag(SHOWN_KEY);
      setFlag(EXIT_CLOSED_KEY); // suppress the exit-intent popup — only one fires
      setOpen(true);
      postPortalEvent(slug, 'portal.survey_shown', { delay_ms: triggerDelayMs });
      trackSurveyStep(slug, 1, TOTAL_STEPS);
    }, triggerDelayMs);

    return () => window.clearTimeout(t);
  }, [slug, alreadyConverted, triggerDelayMs]);

  // ---- ESC to close + lock background scroll while open ----
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close('esc');
    };
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function close(reason: string) {
    if (status !== 'success') {
      postPortalEvent(slug, 'portal.survey_dismissed', { reason, step: stepIdx + 1 });
    }
    setOpen(false);
  }

  function chooseOption(questionId: string, value: string) {
    const next = stepIdx + 1;
    setAnswers((a) => ({ ...a, [questionId]: value }));
    setStepIdx(next);
    trackSurveyStep(slug, next + 1, TOTAL_STEPS);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status === 'submitting') return;
    const data = new FormData(event.currentTarget);
    if (!data.get('gdpr_consent')) {
      setErrorMsg('È necessario accettare il trattamento dei dati per procedere.');
      setStatus('error');
      return;
    }
    const phone = String(data.get('phone') ?? '').trim();
    if (!phone) {
      setErrorMsg('Inserisci un numero di telefono.');
      setStatus('error');
      return;
    }

    setStatus('submitting');
    setErrorMsg(null);
    postPortalEvent(slug, 'portal.appointment_click');
    try {
      await submitSurvey(slug, { answers, phone });
      postPortalEvent(slug, 'portal.survey_submitted', { answers_count: Object.keys(answers).length });
      setStatus('success');
      window.setTimeout(() => setOpen(false), 2400);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Errore inatteso.');
      setStatus('error');
    }
  }

  if (!open) return null;

  const privacyHref = privacyPolicyUrl || `/privacy?slug=${encodeURIComponent(slug)}`;
  const question = stepIdx < QUESTIONS.length ? QUESTIONS[stepIdx] : null;

  return (
    <div
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) close('backdrop');
      }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="survey-title"
        className="relative w-full max-w-md max-h-[90vh] overflow-y-auto rounded-2xl border border-outline-variant bg-surface-container p-6 shadow-xl motion-safe:animate-fade-up"
      >
        <button
          type="button"
          aria-label="Chiudi"
          onClick={() => close('x')}
          className="absolute right-3 top-3 flex h-8 w-8 items-center justify-center rounded-full text-on-surface-variant transition-colors hover:bg-surface-container-high"
        >
          <span aria-hidden className="text-xl leading-none">
            ×
          </span>
        </button>

        {status === 'success' ? (
          <div className="py-6 text-center">
            <p className="font-headline text-xl font-bold text-on-surface">Perfetto, grazie!</p>
            <p className="mt-2 text-sm text-on-surface-variant">
              Un tecnico di {tenantName} ti richiama a breve con l’analisi personalizzata.
            </p>
          </div>
        ) : (
          <>
            {/* Progress dots — "step N of TOTAL" */}
            <div className="flex items-center justify-between">
              <p
                className="text-[11px] font-semibold uppercase tracking-widest"
                style={{ color: accent }}
              >
                Domanda {Math.min(stepIdx + 1, TOTAL_STEPS)} di {TOTAL_STEPS}
              </p>
              <div className="flex gap-1.5">
                {Array.from({ length: TOTAL_STEPS }).map((_, i) => (
                  <span
                    key={i}
                    className="h-2 w-2 rounded-full transition-colors"
                    style={{ backgroundColor: i <= stepIdx ? accent : 'var(--color-outline-variant, #cbd5e1)' }}
                  />
                ))}
              </div>
            </div>

            {question ? (
              <>
                <h2
                  id="survey-title"
                  className="mt-3 font-headline text-2xl font-bold tracking-tight text-on-surface"
                >
                  {question.label}
                </h2>
                <div className="mt-4 space-y-2.5">
                  {question.options.map((opt) => (
                    <button
                      key={opt}
                      type="button"
                      onClick={() => chooseOption(question.id, opt)}
                      className="w-full rounded-lg border border-slate-300 px-4 py-3 text-left text-sm font-medium text-on-surface transition-colors hover:border-slate-500 hover:bg-surface-container-high"
                    >
                      {opt}
                    </button>
                  ))}
                </div>
              </>
            ) : (
              // Final step — the phone.
              <>
                <h2
                  id="survey-title"
                  className="mt-3 font-headline text-2xl font-bold tracking-tight text-on-surface"
                >
                  A che numero vi ricontattiamo?
                </h2>
                <p className="mt-2 text-sm text-on-surface-variant">
                  Un tecnico di {tenantName} ti richiama con l’analisi completa del risparmio sulla
                  tua sede. Nessun impegno.
                </p>
                <form onSubmit={handleSubmit} className="mt-4 space-y-3">
                  <input
                    name="phone"
                    type="tel"
                    placeholder="Telefono"
                    required
                    maxLength={40}
                    defaultValue={defaultPhone ?? ''}
                    className="w-full rounded-md border border-slate-300 px-3 py-2.5 text-sm focus:border-slate-500 focus:outline-none"
                  />
                  <label className="flex cursor-pointer items-start gap-2.5 text-xs text-on-surface-variant">
                    <input
                      type="checkbox"
                      name="gdpr_consent"
                      required
                      className="mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300"
                      style={{ accentColor: brandColor }}
                    />
                    <span>
                      Acconsento al trattamento dei miei dati da parte di {tenantName}, Titolare del
                      trattamento, ai sensi del Reg. UE 2016/679 (GDPR) per essere ricontattato.{' '}
                      <a
                        href={privacyHref}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="underline hover:opacity-80"
                        style={{ color: brandColor }}
                      >
                        Privacy policy
                      </a>
                    </span>
                  </label>
                  {errorMsg ? <p className="text-xs text-red-600">{errorMsg}</p> : null}
                  <button
                    type="submit"
                    disabled={status === 'submitting'}
                    className="w-full rounded-lg px-4 py-3.5 text-base font-bold uppercase tracking-wide text-white shadow-md transition-transform hover:scale-[1.02] disabled:opacity-60"
                    style={{ backgroundColor: accent }}
                  >
                    {status === 'submitting' ? 'Invio in corso…' : 'Sì, richiamatemi →'}
                  </button>
                </form>
              </>
            )}

            <button
              type="button"
              onClick={() => close('no_thanks')}
              className="mt-3 w-full text-center text-xs text-on-surface-variant underline-offset-2 hover:underline"
            >
              No grazie, continuo a guardare
            </button>
          </>
        )}
      </div>
    </div>
  );
}
