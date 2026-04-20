'use client';

import { useState, type FormEvent } from 'react';
import { API_URL } from '@/lib/api';

type Status = 'idle' | 'submitting' | 'success' | 'error';

export function AppointmentForm({
  slug,
  brandColor,
}: {
  slug: string;
  brandColor: string;
}) {
  const [status, setStatus] = useState<Status>('idle');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status === 'submitting') return;
    const form = event.currentTarget;
    const data = new FormData(form);
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
      // Fire-and-forget: we don't await and swallow any errors — the
      // appointment is already recorded server-side; the pixel is
      // attribution data only.
      void fetch(
        `${API_URL}/v1/public/lead/${encodeURIComponent(slug)}/pixel?stage=booked`,
        { keepalive: true },
      ).catch(() => {
        /* silent — pixel is non-critical */
      });
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
