'use client';

import { useState, type FormEvent } from 'react';
import { API_URL } from '@/lib/api';

type Status = 'idle' | 'submitting' | 'success' | 'error';

export function AppointmentForm({
  slug,
  brandColor,
  privacyPolicyUrl,
}: {
  slug: string;
  brandColor: string;
  privacyPolicyUrl?: string | null;
}) {
  const [status, setStatus] = useState<Status>('idle');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

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

    const body = {
      contact_name: String(data.get('contact_name') ?? '').trim(),
      phone: String(data.get('phone') ?? '').trim(),
      email: String(data.get('email') ?? '').trim() || null,
      preferred_time: String(data.get('preferred_time') ?? '').trim() || null,
      notes: String(data.get('notes') ?? '').trim() || null,
    };
    if (!body.contact_name || !body.phone) {
      setErrorMsg('Nome e telefono sono obbligatori.');
      setStatus('error');
      return;
    }
    setStatus('submitting');
    setErrorMsg(null);
    try {
      const res = await fetch(
        `${API_URL}/v1/public/lead/${encodeURIComponent(slug)}/appointment`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        },
      );
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
      }
      setStatus('success');
      form.reset();

      // Fire the conversion pixel (stage=booked) in the background.
      void fetch(
        `${API_URL}/v1/public/lead/${encodeURIComponent(slug)}/pixel?stage=booked`,
        { keepalive: true },
      ).catch(() => { /* silent — pixel is non-critical */ });
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
        Richiesta ricevuta! Vi ricontattiamo entro 48 ore.
      </div>
    );
  }

  const privacyHref = privacyPolicyUrl || '/privacy';

  return (
    <form onSubmit={handleSubmit} className="mt-4 space-y-3">
      <input
        name="contact_name"
        placeholder="Nome e cognome"
        required
        maxLength={120}
        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
      />
      <input
        name="phone"
        type="tel"
        placeholder="Telefono"
        required
        maxLength={40}
        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
      />
      <input
        name="email"
        type="email"
        placeholder="Email (opzionale)"
        maxLength={200}
        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
      />
      <input
        name="preferred_time"
        placeholder="Orario preferito (opzionale)"
        maxLength={120}
        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
      />
      <textarea
        name="notes"
        placeholder="Note (opzionale)"
        rows={2}
        maxLength={1000}
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
          Acconsento al trattamento dei miei dati personali ai sensi del{' '}
          Regolamento UE 2016/679 (GDPR) per essere ricontattato in merito
          alla richiesta di sopralluogo.{' '}
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
        className="w-full rounded-md px-3 py-2 text-sm font-semibold text-white shadow disabled:opacity-60"
        style={{ backgroundColor: brandColor }}
      >
        {status === 'submitting' ? 'Invio in corso…' : 'Richiedi sopralluogo'}
      </button>
    </form>
  );
}
