'use client';

/**
 * Exit-intent popup for the dossier page. When a visitor is ABOUT TO LEAVE
 * (desktop: cursor crosses the top edge toward the tab bar; mobile: a fast
 * upward flick after meaningful scroll depth) we surface a single low-friction
 * CTA — a 2-field contact mini-form ("ti richiamiamo con l'analisi del
 * risparmio") that reuses the appointment endpoint. Shows ONCE per session,
 * never after a conversion. The bolletta upload stays the on-page CTA.
 */

import { useEffect, useRef, useState, type FormEvent } from 'react';

import { submitAppointment } from '@/lib/appointment';
import { postPortalEvent } from '@/lib/tracking';

type Status = 'idle' | 'submitting' | 'success' | 'error';

const SHOWN_KEY = 'solarLead.exitIntent.shown';
const CLOSED_KEY = 'solarLead.exitIntent.closed';

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
    /* private-mode Safari throws — fine, the in-memory ref still guards */
  }
}

export function ExitIntentModal({
  slug,
  brandColor,
  accentColor,
  tenantName,
  privacyPolicyUrl,
  alreadyConverted,
}: {
  slug: string;
  brandColor: string;
  accentColor?: string;
  tenantName: string;
  privacyPolicyUrl?: string | null;
  alreadyConverted: boolean;
}) {
  const accent = accentColor || brandColor;
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<Status>('idle');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  // Email the prospect left (if any) — drives the "proposta inviata via email"
  // line in the success state.
  const [resentEmail, setResentEmail] = useState<string | null>(null);
  const shownRef = useRef(false);
  const triggerRef = useRef<string>('desktop_mouseout');

  // ---- Exit-intent detection (desktop mouseout + mobile fast scroll-up) ----
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (alreadyConverted) return;
    if (flag(SHOWN_KEY) || flag(CLOSED_KEY)) return;

    let maxDepth = 0;
    let lastY = window.scrollY;

    const trigger = (reason: string) => {
      if (shownRef.current) return;
      shownRef.current = true;
      triggerRef.current = reason;
      setFlag(SHOWN_KEY);
      setOpen(true);
      postPortalEvent(slug, 'portal.exit_intent_shown', { trigger: reason });
      cleanup();
    };

    const onMouseOut = (e: MouseEvent) => {
      // Cursor left the viewport through the TOP edge (toward the tab/address
      // bar) — the canonical desktop abandon signal. relatedTarget===null means
      // it left the document, not just hopped to a child element.
      if (e.relatedTarget === null && e.clientY <= 0) trigger('desktop_mouseout');
    };

    const onScroll = () => {
      const y = window.scrollY;
      const docH = document.documentElement.scrollHeight - window.innerHeight;
      const depth = docH > 0 ? y / docH : 0;
      if (depth > maxDepth) maxDepth = depth;
      const velocityUp = lastY - y; // > 0 means scrolling upward
      lastY = y;
      // Touch has no mouseout: a fast upward flick after the visitor has read
      // a meaningful chunk approximates "seen enough, leaving". Conservative
      // thresholds — better to miss than to annoy.
      if (window.innerWidth < 768 && maxDepth >= 0.35 && velocityUp > 60) {
        trigger('mobile_scrollup');
      }
    };

    function cleanup() {
      document.removeEventListener('mouseout', onMouseOut);
      window.removeEventListener('scroll', onScroll);
    }

    document.addEventListener('mouseout', onMouseOut);
    window.addEventListener('scroll', onScroll, { passive: true });
    return cleanup;
  }, [slug, alreadyConverted]);

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
    setFlag(CLOSED_KEY);
    if (status !== 'success') {
      postPortalEvent(slug, 'portal.exit_intent_dismissed', { reason });
    }
    setOpen(false);
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
    const contact_name = String(data.get('contact_name') ?? '').trim();
    const phone = String(data.get('phone') ?? '').trim();
    // Email is OPTIONAL: phone alone is enough to be recontacted. When present,
    // the backend re-sends the exact proposal to it automatically.
    const email = String(data.get('email') ?? '').trim() || null;
    if (!contact_name || !phone) {
      setErrorMsg('Nome e telefono sono obbligatori.');
      setStatus('error');
      return;
    }
    // Email is optional, but if present it must satisfy the backend regex —
    // otherwise the whole 202 (including the phone recontact) 422s. Mirror the
    // server pattern here so a typo surfaces a clear message instead of
    // silently sinking the submission.
    if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      setErrorMsg('L’email inserita non è valida. Correggila o lasciala vuota.');
      setStatus('error');
      return;
    }

    setStatus('submitting');
    setErrorMsg(null);
    // Same high-intent hand-raise the on-page form fires (+50 engagement).
    postPortalEvent(slug, 'portal.appointment_click');

    try {
      await submitAppointment(slug, {
        contact_name,
        phone,
        email,
        preferred_time: null,
        notes: null,
      });
      setFlag(CLOSED_KEY);
      setResentEmail(email);
      postPortalEvent(slug, 'portal.exit_intent_submitted', {
        trigger: triggerRef.current,
        with_email: Boolean(email),
      });
      setStatus('success');
      window.setTimeout(() => setOpen(false), 2400);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Errore inatteso.');
      setStatus('error');
    }
  }

  if (!open) return null;

  const privacyHref = privacyPolicyUrl || `/privacy?slug=${encodeURIComponent(slug)}`;

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
        aria-labelledby="exit-intent-title"
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
              Ti richiamiamo a breve con l’analisi del tuo risparmio.
            </p>
            {resentEmail ? (
              <p className="mt-2 text-sm text-on-surface-variant">
                Ti abbiamo inviato la proposta anche via email a{' '}
                <span className="font-semibold text-on-surface">{resentEmail}</span>.
              </p>
            ) : null}
          </div>
        ) : (
          <>
            <p
              className="text-[11px] font-semibold uppercase tracking-widest"
              style={{ color: accent }}
            >
              Aspetta un attimo
            </p>
            <h2
              id="exit-intent-title"
              className="mt-1 font-headline text-2xl font-bold tracking-tight text-on-surface"
            >
              Vuoi sapere quanto risparmieresti?
            </h2>
            <p className="mt-2 text-sm text-on-surface-variant">
              Lascia nome e numero: un tecnico di {tenantName} ti richiama con l’analisi
              completa del risparmio sulla tua sede. Vuoi anche la proposta scritta?
              Aggiungi l’email e te la inviamo subito.
            </p>

            <form onSubmit={handleSubmit} className="mt-4 space-y-3">
              <input
                name="contact_name"
                placeholder="Nome e cognome"
                required
                maxLength={120}
                className="w-full rounded-md border border-slate-300 px-3 py-2.5 text-sm focus:border-slate-500 focus:outline-none"
              />
              <input
                name="phone"
                type="tel"
                placeholder="Telefono"
                required
                maxLength={40}
                className="w-full rounded-md border border-slate-300 px-3 py-2.5 text-sm focus:border-slate-500 focus:outline-none"
              />
              <input
                name="email"
                type="email"
                placeholder="Email (opzionale — ricevi subito la proposta)"
                maxLength={200}
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
                  Acconsento al trattamento dei miei dati da parte di {tenantName}, Titolare
                  del trattamento, ai sensi del Reg. UE 2016/679 (GDPR) per essere ricontattato.{' '}
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

              <button
                type="button"
                onClick={() => close('no_thanks')}
                className="w-full text-center text-xs text-on-surface-variant underline-offset-2 hover:underline"
              >
                No grazie, continuo a guardare
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
