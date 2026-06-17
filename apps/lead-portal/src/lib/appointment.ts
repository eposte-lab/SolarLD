import { API_URL } from './api';

export type AppointmentBody = {
  contact_name: string;
  phone: string;
  email?: string | null;
  preferred_time?: string | null;
  notes?: string | null;
};

/**
 * POST a contact / appointment request to the public API, then fire the
 * conversion pixel (non-critical, background). Shared by the in-dossier
 * `AppointmentForm` and the exit-intent modal so the endpoint contract lives
 * in ONE place. Throws on a non-OK response; the caller owns field validation,
 * GDPR-consent guarding, and UI state.
 */
export async function submitAppointment(slug: string, body: AppointmentBody): Promise<void> {
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
  // Conversion pixel (stage=booked) — fire-and-forget; never blocks the caller.
  void fetch(
    `${API_URL}/v1/public/lead/${encodeURIComponent(slug)}/pixel?stage=booked`,
    { keepalive: true },
  ).catch(() => {
    /* silent — pixel is non-critical */
  });
}
