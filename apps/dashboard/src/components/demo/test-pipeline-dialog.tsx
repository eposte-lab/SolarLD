'use client';

/**
 * Demo "Avvia test pipeline" dialog.
 *
 * Flow:
 *   1. User opens the dialog (banner button) and sees a form pre-filled
 *      with a plausible Italian B2B example.
 *   2. They edit the address; we hit `/v1/demo/geocode-preview` on blur
 *      to show the resolved formatted address + relevance.
 *   3. On submit we `POST /v1/demo/test-pipeline`. The endpoint runs
 *      scoring → creative → outreach synchronously (~90s wall clock)
 *      and returns `{ lead_id, public_slug, attempts_remaining }`.
 *   4. We close the dialog and surface a persistent toast with a
 *      "Vai al lead →" deep link to `/leads/{lead_id}`.
 *
 * Implementation notes:
 *   - We use a native `<dialog>` element (open/close imperatively via
 *     `showModal()` / `close()`) rather than pulling in a heavier
 *     dialog primitive. The repo doesn't have a shared Modal yet and
 *     YAGNI'ing one for a single demo surface keeps the bundle small.
 *   - All fetch calls go through the Supabase browser session for
 *     auth — same pattern as `branding-editor.tsx`.
 *   - We deliberately keep the form simple and uncontrolled where
 *     practical: the only field that needs live validation is the
 *     address (geocode preview), the rest are submit-time validated.
 */

import { useEffect, useRef, useState } from 'react';
import { Loader2, MapPin, Rocket, ArrowRight } from 'lucide-react';

import { GradientButton } from '@/components/ui/gradient-button';
import { createBrowserClient } from '@/lib/supabase/client';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// Plausible default — MULTILOG S.P.A. (Agglomerato ASI Pascarola, NA),
// trasporto merci su strada (ATECO 49.41), €37.5M fatturato, 48
// dipendenti. Real azienda the prospect can recognise during a sales
// call, with enough surface area (capannoni industriali) to make the
// rooftop rendering look impressive. Decision-maker / recipient email
// stay blank — the prospect must type a real inbox they own to see
// the test email actually land.
const DEFAULT_FORM = {
  vat_number: '09881610019',
  legal_name: 'MULTILOG S.P.A.',
  ateco_code: '49.41',
  hq_address: 'Zona Industriale ASI, 80023 Agglomerato Asi Pascarola NA',
  decision_maker_name: 'Antonio De Luca',
  decision_maker_role: 'Amministratore Delegato',
  decision_maker_email: 'multilogspa@pec.it',
  recipient_email: '',
};

type FormState = typeof DEFAULT_FORM;

interface GeocodePreview {
  found: boolean;
  formatted?: string | null;
  cap?: string | null;
  comune?: string | null;
  provincia?: string | null;
  relevance?: number | null;
  notes?: string | null;
}

async function authHeader(): Promise<Record<string, string>> {
  if (typeof window === 'undefined') return {};
  const sb = createBrowserClient();
  const {
    data: { session },
  } = await sb.auth.getSession();
  if (!session?.access_token) return {};
  return { Authorization: `Bearer ${session.access_token}` };
}

export function TestPipelineDialog({
  attemptsRemaining,
}: {
  attemptsRemaining: number;
}) {
  const ref = useRef<HTMLDialogElement | null>(null);
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [geocode, setGeocode] = useState<GeocodePreview | null>(null);
  const [geocoding, setGeocoding] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitStep, setSubmitStep] = useState<
    'idle' | 'scoring' | 'rendering' | 'sending' | 'done'
  >('idle');
  const [success, setSuccess] = useState<{
    lead_id: string;
    attempts_remaining: number;
  } | null>(null);

  function open() {
    setError(null);
    setSuccess(null);
    setSubmitStep('idle');
    ref.current?.showModal();
  }

  function close() {
    ref.current?.close();
  }

  // Show a single "Scoring…" label while the request is in flight.
  // The endpoint now returns 202 after scoring (~5s) and runs creative
  // + outreach in the background — we don't need to fake a multi-step
  // progress UI here, the lead detail timeline picks up the rendering
  // and send events live.
  useEffect(() => {
    if (!submitting) return;
    setSubmitStep('scoring');
  }, [submitting]);

  async function handleGeocodeBlur() {
    if (!form.hq_address || form.hq_address.trim().length < 6) {
      setGeocode(null);
      return;
    }
    setGeocoding(true);
    try {
      const auth = await authHeader();
      const res = await fetch(`${API_URL}/v1/demo/geocode-preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...auth },
        body: JSON.stringify({ address: form.hq_address }),
      });
      const data = (await res.json().catch(() => null)) as GeocodePreview | null;
      setGeocode(data);
    } catch {
      setGeocode({ found: false, notes: 'Errore di rete sulla preview.' });
    } finally {
      setGeocoding(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const auth = await authHeader();
      const res = await fetch(`${API_URL}/v1/demo/test-pipeline`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...auth },
        body: JSON.stringify(form),
      });
      const data = (await res.json().catch(() => null)) as
        | {
            lead_id?: string;
            attempts_remaining?: number;
            detail?: string;
          }
        | null;
      if (!res.ok || !data?.lead_id) {
        setError(
          data?.detail ??
            `Errore (${res.status}). Riprova fra qualche secondo.`,
        );
        setSubmitting(false);
        return;
      }
      setSuccess({
        lead_id: data.lead_id,
        attempts_remaining: data.attempts_remaining ?? 0,
      });
      setSubmitStep('done');
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Errore imprevisto. Riprova.',
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <GradientButton
        size="sm"
        variant="primary"
        onClick={open}
        disabled={attemptsRemaining <= 0}
      >
        <Rocket size={14} strokeWidth={2.5} className="mr-1.5" />
        Avvia test
      </GradientButton>

      <dialog
        ref={ref}
        className="m-auto w-full max-w-xl rounded-2xl bg-surface p-0 shadow-ambient-lg backdrop:bg-on-surface/50 backdrop:backdrop-blur-sm"
        onClose={() => {
          // Reset the success state when the user dismisses, so the
          // next open shows the form again rather than the success card.
          setSuccess(null);
          setSubmitStep('idle');
        }}
      >
        <div className="space-y-4 p-6">
          <header className="flex items-start justify-between">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Test pipeline · {attemptsRemaining}/3 rimanenti
              </p>
              <h2 className="mt-1 font-headline text-2xl font-bold tracking-tight">
                Avvia un test reale
              </h2>
              <p className="mt-1 text-xs text-on-surface-variant">
                Lead pronto in ~5 secondi · rendering tetto + invio email
                continuano in background (visibili live nella scheda del lead).
              </p>
            </div>
            <button
              type="button"
              onClick={close}
              className="rounded-full p-1 text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
              aria-label="Chiudi"
            >
              ✕
            </button>
          </header>

          {success ? (
            <SuccessPanel
              leadId={success.lead_id}
              attemptsRemaining={success.attempts_remaining}
              onClose={close}
            />
          ) : (
            <form className="space-y-4" onSubmit={handleSubmit}>
              <div className="grid grid-cols-2 gap-3">
                <Field label="P.IVA" required>
                  <input
                    name="vat_number"
                    required
                    minLength={5}
                    value={form.vat_number}
                    onChange={(e) =>
                      setForm({ ...form, vat_number: e.target.value })
                    }
                    className={inputClass}
                  />
                </Field>
                <Field label="Codice ATECO">
                  <input
                    name="ateco_code"
                    value={form.ateco_code}
                    onChange={(e) =>
                      setForm({ ...form, ateco_code: e.target.value })
                    }
                    className={inputClass}
                    placeholder="es. 25.11"
                  />
                </Field>
              </div>

              <Field label="Ragione sociale" required>
                <input
                  name="legal_name"
                  required
                  value={form.legal_name}
                  onChange={(e) =>
                    setForm({ ...form, legal_name: e.target.value })
                  }
                  className={inputClass}
                />
              </Field>

              <Field
                label="Indirizzo HQ (singolo campo, riconoscimento automatico)"
                required
              >
                <input
                  name="hq_address"
                  required
                  value={form.hq_address}
                  onChange={(e) =>
                    setForm({ ...form, hq_address: e.target.value })
                  }
                  onBlur={handleGeocodeBlur}
                  className={inputClass}
                  placeholder="Via Roma 12, 20100 Milano MI"
                />
                <GeocodeBadge
                  geocoding={geocoding}
                  preview={geocode}
                />
              </Field>

              <div className="grid grid-cols-2 gap-3">
                <Field label="Decision-maker · nome" required>
                  <input
                    required
                    value={form.decision_maker_name}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        decision_maker_name: e.target.value,
                      })
                    }
                    className={inputClass}
                  />
                </Field>
                <Field label="Ruolo">
                  <input
                    value={form.decision_maker_role}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        decision_maker_role: e.target.value,
                      })
                    }
                    className={inputClass}
                    placeholder="es. Amministratore Delegato"
                  />
                </Field>
              </div>

              <Field
                label="Email decision-maker (anagrafica)"
                required
              >
                <input
                  type="email"
                  required
                  value={form.decision_maker_email}
                  onChange={(e) =>
                    setForm({ ...form, decision_maker_email: e.target.value })
                  }
                  className={inputClass}
                />
              </Field>

              <Field
                label="Email destinatario (dove arriva il test)"
                required
                helper="Usa una tua casella reale per vedere atterrare l&apos;email."
              >
                <input
                  type="email"
                  required
                  value={form.recipient_email}
                  onChange={(e) =>
                    setForm({ ...form, recipient_email: e.target.value })
                  }
                  className={inputClass}
                />
              </Field>

              {error && (
                <p
                  className="rounded-lg bg-error-container px-3 py-2 text-xs text-on-error-container"
                  role="alert"
                >
                  {error}
                </p>
              )}

              <div className="flex items-center justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={close}
                  disabled={submitting}
                  className="rounded-full px-4 py-2 text-xs font-semibold text-on-surface-variant hover:bg-surface-container-high disabled:opacity-50"
                >
                  Annulla
                </button>
                <GradientButton
                  type="submit"
                  size="sm"
                  variant="primary"
                  disabled={submitting}
                >
                  {submitting ? (
                    <span className="inline-flex items-center gap-2">
                      <Loader2
                        size={14}
                        strokeWidth={2.5}
                        className="animate-spin"
                      />
                      {submitStep === 'scoring'
                        ? 'Scoring…'
                        : submitStep === 'rendering'
                          ? 'Rendering tetto…'
                          : submitStep === 'sending'
                            ? 'Invio email…'
                            : 'Avvio…'}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5">
                      <Rocket size={14} strokeWidth={2.5} />
                      Avvia pipeline
                    </span>
                  )}
                </GradientButton>
              </div>
            </form>
          )}
        </div>
      </dialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SuccessPanel({
  leadId,
  attemptsRemaining,
  onClose,
}: {
  leadId: string;
  attemptsRemaining: number;
  onClose: () => void;
}) {
  return (
    <div className="space-y-3 rounded-xl bg-primary/5 p-4 ring-1 ring-primary/15">
      <p className="font-headline text-lg font-bold text-primary">
        Lead generato 🎉
      </p>
      <p className="text-sm text-on-surface">
        Il rendering del tetto e l&apos;invio dell&apos;email sono partiti in
        background (~90s). Apri la scheda del lead per vedere
        l&apos;anagrafica completa e — appena pronti — il rendering, lo stato
        di invio e gli eventi di tracking in tempo reale.
      </p>
      <p className="text-xs text-on-surface-variant">
        Tentativi rimanenti: <strong>{attemptsRemaining}/3</strong>.
      </p>
      <div className="flex items-center justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onClose}
          className="rounded-full px-3 py-1.5 text-xs font-semibold text-on-surface-variant hover:bg-surface-container-high"
        >
          Chiudi
        </button>
        <a
          href={`/leads/${leadId}`}
          className="inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-xs font-semibold text-on-primary shadow-ambient-sm hover:shadow-ambient-md"
        >
          Vai al lead
          <ArrowRight size={14} strokeWidth={2.5} />
        </a>
      </div>
    </div>
  );
}

function GeocodeBadge({
  geocoding,
  preview,
}: {
  geocoding: boolean;
  preview: GeocodePreview | null;
}) {
  if (geocoding) {
    return (
      <p className="mt-1.5 inline-flex items-center gap-1.5 text-[11px] text-on-surface-variant">
        <Loader2 size={12} strokeWidth={2.5} className="animate-spin" />
        Riconoscimento indirizzo in corso…
      </p>
    );
  }
  if (!preview) return null;
  if (!preview.found) {
    return (
      <p className="mt-1.5 text-[11px] text-warning">
        {preview.notes ??
          'Indirizzo non riconosciuto. Aggiungi numero civico o CAP.'}
      </p>
    );
  }
  return (
    <p className="mt-1.5 inline-flex flex-wrap items-center gap-1.5 text-[11px] text-on-surface-variant">
      <MapPin size={12} strokeWidth={2.25} className="text-primary" />
      <span className="text-on-surface">{preview.formatted}</span>
      {typeof preview.relevance === 'number' && (
        <span className="rounded-full bg-surface-container-low px-1.5 py-0.5 text-[10px] uppercase tracking-wide">
          relev. {preview.relevance.toFixed(2)}
        </span>
      )}
    </p>
  );
}

function Field({
  label,
  required,
  helper,
  children,
}: {
  label: string;
  required?: boolean;
  helper?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-on-surface-variant">
        {label}
        {required && <span className="ml-0.5 text-error">*</span>}
      </span>
      {children}
      {helper && (
        <span className="text-[10px] text-on-surface-variant">{helper}</span>
      )}
    </label>
  );
}

const inputClass =
  'rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30';
