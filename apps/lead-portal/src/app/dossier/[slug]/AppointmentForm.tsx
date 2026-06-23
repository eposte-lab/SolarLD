'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { submitAppointment } from '@/lib/appointment';
import { postPortalEvent } from '@/lib/tracking';

type Status = 'idle' | 'submitting' | 'success' | 'error';

export function AppointmentForm({
  slug,
  brandColor,
  accentColor,
  privacyPolicyUrl,
  tenantName,
  defaultPhone,
  trackContactView = false,
}: {
  slug: string;
  brandColor: string;
  /** Vivid accent for the primary CTA button. Falls back to brandColor. */
  accentColor?: string;
  privacyPolicyUrl?: string | null;
  /** Titolare del trattamento — nominato nel testo del consenso. */
  tenantName: string;
  /**
   * Phone we already scraped for this lead — pre-fills the phone field so the
   * prospect only confirms/corrects it. The NAME is the one thing scraping
   * can't give us, so it's the only field they actually have to type. Null
   * when we have no number on file → the field just starts empty.
   */
  defaultPhone?: string | null;
  /**
   * Fire ``portal.contact_view`` on mount. Set on the standalone
   * ``/contatto`` page (the follow-up CTA destination) so we can tell a
   * lead who navigated to the contact form apart from one who merely
   * scrolled the dossier. In the in-dossier form leave it false — the
   * dossier already fires ``portal.view`` and we don't want every page
   * load to count as a contact-form open.
   */
  trackContactView?: boolean;
}) {
  const accent = accentColor || brandColor;
  const [status, setStatus] = useState<Status>('idle');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  // Guards: ``contact_view`` fires once per mount; ``contact_started``
  // once on the first keystroke; ``dirtyRef`` marks unsent edits so the
  // abandon-capture sends the LATEST draft (not a stale one) and doesn't
  // spam on every tab switch; ``submittedRef`` suppresses the abandon
  // capture once the form was actually sent.
  const startedRef = useRef(false);
  const dirtyRef = useRef(false);
  const submittedRef = useRef(false);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (!trackContactView) return;
    postPortalEvent(slug, 'portal.contact_view');
  }, [slug, trackContactView]);

  function handleInput() {
    dirtyRef.current = true;
    if (startedRef.current) return;
    startedRef.current = true;
    postPortalEvent(slug, 'portal.contact_started');
  }

  // Abandon-capture: if the lead typed something and then leaves WITHOUT
  // submitting, beacon the partial values + the GDPR-consent state.
  //
  // ⚠️ GDPR: this persists personal data the visitor did NOT submit. The
  // operator (data controller) explicitly opted into collecting abandoned
  // drafts as lead intelligence. ``gdpr_consent`` records whether the
  // consent box was ticked at the moment they left, so the controller can
  // decide whether follow-up on this draft is lawful.
  useEffect(() => {
    const captureDraft = () => {
      if (submittedRef.current || !startedRef.current || !dirtyRef.current) return;
      const form = formRef.current;
      if (!form) return;
      dirtyRef.current = false;
      const data = new FormData(form);
      const val = (k: string) => String(data.get(k) ?? '').trim();
      postPortalEvent(slug, 'portal.contact_abandoned', {
        contact_name: val('contact_name'),
        phone: val('phone'),
        gdpr_consent: Boolean(data.get('gdpr_consent')),
      });
    };
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') captureDraft();
    };
    window.addEventListener('pagehide', captureDraft);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.removeEventListener('pagehide', captureDraft);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [slug]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status === 'submitting') return;
    const form = event.currentTarget;
    const data = new FormData(form);

    // GDPR consent is a required checkbox — the `required` attribute
    // handles browser-side validation, but we guard here too.
    if (!data.get('gdpr_consent')) {
      setErrorMsg('È necessario accettare il trattamento dei dati per procedere.');
      setStatus('error');
      return;
    }

    // We already have the email on file — the form no longer asks for it. The
    // only fields are the name (what scraping can't give us) and the phone we
    // pre-filled. Email/preferred_time/notes are left to the backend defaults.
    const body = {
      contact_name: String(data.get('contact_name') ?? '').trim(),
      phone: String(data.get('phone') ?? '').trim(),
    };
    if (!body.contact_name || !body.phone) {
      setErrorMsg('Nome e telefono sono obbligatori.');
      setStatus('error');
      return;
    }
    setStatus('submitting');
    setErrorMsg(null);

    // High-intent signal: the lead clicked "Contattaci subito" with a
    // valid name + phone. This is the strongest hand-raise in the funnel
    // (+50 engagement → "caldo"). Fired before the network call so it
    // lands even if the POST below is cut short by a fast navigation.
    postPortalEvent(slug, 'portal.appointment_click');

    try {
      await submitAppointment(slug, body);
      submittedRef.current = true;
      dirtyRef.current = false;
      // The visitor just converted here — suppress the exit-intent popup for
      // the rest of the session (pipeline_status only refreshes on reload, so
      // cover the in-session case with the same flag the modal reads).
      try {
        window.sessionStorage.setItem('solarLead.exitIntent.closed', '1');
      } catch {
        /* private-mode Safari — non-critical */
      }
      setStatus('success');
      form.reset();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Errore inatteso.');
      setStatus('error');
    }
  }

  if (status === 'success') {
    return (
      <div
        className="mt-4 rounded-md p-4 text-sm"
        style={{ backgroundColor: `${brandColor}15`, color: brandColor }}
      >
        Perfetto! Preparo il preventivo personalizzato e ti ricontatto entro 48 ore.
      </div>
    );
  }

  const privacyHref =
    privacyPolicyUrl || `/privacy?slug=${encodeURIComponent(slug)}`;

  return (
    <form ref={formRef} onSubmit={handleSubmit} onInput={handleInput} className="mt-4 space-y-3">
      {/* The ask, reframed as a benefit: we already have the contact details
          (email + phone) from the dossier — the ONE thing we're missing is who
          to make the quote out to. That name is also exactly what we need for
          a warm phone follow-up, so it's the single required field. */}
      <p className="text-base font-semibold text-on-surface">
        Preparo il preventivo personalizzato — a nome di chi lo intesto?
      </p>
      <input
        name="contact_name"
        placeholder="Nome e cognome del referente"
        required
        autoFocus
        maxLength={120}
        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
      />
      {/* Phone pre-filled from what we scraped — they just confirm or fix it. */}
      <input
        name="phone"
        type="tel"
        placeholder="Telefono"
        required
        maxLength={40}
        defaultValue={defaultPhone ?? ''}
        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
      />

      {/* GDPR consent — required */}
      <label className="flex cursor-pointer items-start gap-2.5 text-xs text-on-surface-variant">
        <input
          type="checkbox"
          name="gdpr_consent"
          required
          className="mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300"
          style={{ accentColor: brandColor }}
        />
        <span>
          Acconsento al trattamento dei miei dati personali da parte di{' '}
          {tenantName}, Titolare del trattamento, ai sensi del Regolamento
          UE 2016/679 (GDPR) per essere ricontattato in merito alla
          richiesta di sopralluogo.{' '}
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
        {status === 'submitting' ? 'Invio in corso…' : 'Preparami il preventivo →'}
      </button>
    </form>
  );
}
