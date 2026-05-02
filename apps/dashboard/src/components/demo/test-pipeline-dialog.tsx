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
import {
  AlertTriangle,
  ArrowRight,
  Building,
  Check,
  Loader2,
  MapPin,
  Rocket,
} from 'lucide-react';

import { GradientButton } from '@/components/ui/gradient-button';
import { createBrowserClient } from '@/lib/supabase/client';
import { cn } from '@/lib/utils';
import {
  BuildingPicker,
  type PickerCandidate,
} from '@/components/demo/building-picker';

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
  // UUID of the chosen tenant_inboxes row (the "From" sender). Empty
  // string means "fall back to round-robin / legacy". The dialog
  // populates this with the first available inbox once the
  // /v1/demo/inboxes fetch resolves.
  inbox_id: '',
};

type FormState = typeof DEFAULT_FORM;

interface InboxOption {
  id: string;
  email: string;
  display_name: string | null;
}

// Mirror of the API's IdentifyBuildingResponse (see routes/demo.py).
// Confidence buckets match `subjects.sede_operativa_confidence`.
interface BicResult {
  confidence: 'high' | 'medium' | 'low' | 'none' | 'user_confirmed';
  needs_user_confirmation: boolean;
  lat: number | null;
  lng: number | null;
  address: string | null;
  source: string;
  source_chain: Record<string, unknown>[];
  candidates: PickerCandidate[];
  cached: boolean;
}

interface GeocodePreview {
  found: boolean;
  formatted?: string | null;
  cap?: string | null;
  comune?: string | null;
  provincia?: string | null;
  relevance?: number | null;
  notes?: string | null;
}

// Server-side state machine, mirrored from `demo_pipeline_runs.status`.
// The dialog polls GET /v1/demo/pipeline-runs/{run_id} until it sees
// `done` (success toast) or `failed` (error panel + refund). Anything
// in between is rendered as a step indicator so the user knows the
// pipeline is still progressing — no more "Lead creato!" toast for an
// email that never went out.
type RunStatus =
  | 'scoring'
  | 'creative'
  | 'outreach'
  | 'done'
  | 'failed';

/**
 * Outreach send statuses we recognise from the API. Mirrors the API's
 * ``CampaignStatus`` enum plus the in-flight states the TrackingAgent
 * writes when Resend webhook events arrive (``DELIVERED`` / ``FAILED``
 * are the only ones the user-facing copy distinguishes).
 */
type EmailStatus =
  | 'SCHEDULED'
  | 'SENT'
  | 'DELIVERED'
  | 'OPENED'
  | 'CLICKED'
  | 'FAILED';

interface RunSnapshot {
  id: string;
  lead_id: string | null;
  status: RunStatus;
  failed_step: string | null;
  error_message: string | null;
  notes: string | null;
  updated_at: string;
  // Delivery truth — populated even after the pipeline reaches ``done``.
  // The dialog keeps polling on done until ``email_status`` becomes
  // DELIVERED/FAILED so the toast tells the user the *real* outcome
  // instead of a misleading "Email inviata 🎉" the moment Resend's API
  // returned 2xx.
  email_status: EmailStatus | null;
  email_status_detail: string | null;
  email_recipient: string | null;
  email_message_id: string | null;
  // Roof identification provenance — exposed for completeness.  The
  // dialog could surface a "tetto da verificare" warning for low-confidence
  // runs in a future iteration; for now we just include the fields so
  // the polling response stays in sync with the admin endpoint.
  roof_source: string | null;
  roof_confidence: 'high' | 'medium' | 'low' | 'none' | null;
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
  const [run, setRun] = useState<RunSnapshot | null>(null);
  const [attemptsAfterRun, setAttemptsAfterRun] = useState<number | null>(null);
  const [inboxes, setInboxes] = useState<InboxOption[]>([]);
  const [inboxesLoading, setInboxesLoading] = useState(true);

  // ─── Building Identification Cascade (BIC) state ────────────────
  // Triggered manually via the "Identifica capannone" button (we don't
  // run the cascade on every keystroke — Vision API is ~$0.025/call).
  // The button is enabled once vat_number + legal_name + hq_address
  // are all filled.
  const [bicLoading, setBicLoading] = useState(false);
  const [bicResult, setBicResult] = useState<BicResult | null>(null);
  // Set once the user has either confirmed a candidate from the picker
  // OR the cascade returned high/medium confidence and the user tacitly
  // accepted by clicking "Avvia pipeline". When non-null, /test-pipeline
  // submits this lat/lng and the backend short-circuits the cascade.
  const [confirmedBuilding, setConfirmedBuilding] = useState<
    { lat: number; lng: number } | null
  >(null);

  // Fetch the available senders (tenant_inboxes rows) once on mount so
  // the dialog can render a dropdown. We refetch nothing — inbox config
  // changes rarely and a stale list is recoverable via the 422 the
  // backend returns when the chosen UUID is no longer active.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const auth = await authHeader();
        const res = await fetch(`${API_URL}/v1/demo/inboxes`, {
          headers: { ...auth },
        });
        if (!res.ok) {
          if (!cancelled) setInboxesLoading(false);
          return;
        }
        const data = (await res.json().catch(() => null)) as
          | { inboxes?: InboxOption[] }
          | null;
        if (cancelled) return;
        const list = data?.inboxes ?? [];
        setInboxes(list);
        // Pre-select the first inbox so the prospect doesn't have to
        // open the dropdown to pick one — they can still change it.
        const first = list[0];
        if (first) {
          setForm((prev) =>
            prev.inbox_id ? prev : { ...prev, inbox_id: first.id },
          );
        }
      } catch {
        // Soft-fail: leave the list empty. The form still submits and
        // the backend falls back to the legacy single-inbox path.
      } finally {
        if (!cancelled) setInboxesLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  function open() {
    setError(null);
    setRun(null);
    setAttemptsAfterRun(null);
    ref.current?.showModal();
  }

  function close() {
    ref.current?.close();
  }

  // Poll the demo_pipeline_runs row for up to ~3 minutes after submit.
  //
  // Pipeline terminal states are ``done`` and ``failed``, but we keep
  // polling on ``done`` until ``email_status`` reaches a terminal value
  // (DELIVERED / OPENED / CLICKED / FAILED). That delivery confirmation
  // only lands when the Resend webhook fires, so it's the only signal
  // that proves the email *actually arrived* in the recipient's inbox
  // (vs. just being accepted by the Resend API). On ``failed`` we stop
  // immediately — the user has already been refunded and no email will
  // ever be sent.
  useEffect(() => {
    if (!run) return;
    const isEmailTerminal =
      run.email_status === 'DELIVERED' ||
      run.email_status === 'OPENED' ||
      run.email_status === 'CLICKED' ||
      run.email_status === 'FAILED';
    if (run.status === 'failed') return;
    if (run.status === 'done' && isEmailTerminal) return;

    const runId = run.id;
    let cancelled = false;
    const start = Date.now();
    const tick = async () => {
      if (cancelled) return;
      try {
        const auth = await authHeader();
        const res = await fetch(
          `${API_URL}/v1/demo/pipeline-runs/${runId}`,
          { headers: { ...auth } },
        );
        if (res.ok) {
          const next = (await res.json()) as RunSnapshot;
          if (!cancelled) setRun(next);
          if (next.status === 'failed') return;
          const nextEmailTerminal =
            next.email_status === 'DELIVERED' ||
            next.email_status === 'OPENED' ||
            next.email_status === 'CLICKED' ||
            next.email_status === 'FAILED';
          if (next.status === 'done' && nextEmailTerminal) return;
        }
      } catch {
        // Swallow transient errors — we'll retry on the next tick.
      }
      // Bail after 10 minutes so we don't poll forever on a stuck job
      // or a webhook that never arrives. 10 min is the realistic upper
      // bound: Solar + OSM-snap retry (~30s) + nano-banana paint
      // (~60-90s) + Kling 1.6-Pro video render (~90-300s, queue
      // dependent) + Remotion encode/upload (~10s) + outreach (~5s).
      // The Resend webhook arrives within ~1 min after the SENT
      // status flip on a healthy domain, so 10 min covers the whole
      // happy path with margin.
      if (Date.now() - start > 600_000) return;
      if (!cancelled) setTimeout(tick, 2_000);
    };
    const t = setTimeout(tick, 2_000);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [run]);

  async function handleIdentifyBuilding() {
    if (
      !form.vat_number ||
      !form.legal_name ||
      !form.hq_address ||
      form.hq_address.trim().length < 6
    ) {
      setBicResult({
        confidence: 'none',
        needs_user_confirmation: true,
        lat: null,
        lng: null,
        address: null,
        source: 'unresolved',
        source_chain: [],
        candidates: [],
        cached: false,
      });
      return;
    }
    setBicLoading(true);
    setConfirmedBuilding(null);
    try {
      const auth = await authHeader();
      const res = await fetch(`${API_URL}/v1/demo/identify-building`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...auth },
        body: JSON.stringify({
          vat_number: form.vat_number,
          legal_name: form.legal_name,
          hq_address: form.hq_address,
          ateco_code: form.ateco_code || null,
        }),
      });
      const data = (await res.json().catch(() => null)) as BicResult | null;
      if (!res.ok || !data) {
        setBicResult({
          confidence: 'none',
          needs_user_confirmation: true,
          lat: null,
          lng: null,
          address: null,
          source: 'unresolved',
          source_chain: [],
          candidates: [],
          cached: false,
        });
        return;
      }
      setBicResult(data);
      // High / medium / user_confirmed → tacit acceptance: pre-fill the
      // confirmed lat/lng so submit short-circuits the cascade. Low /
      // none → wait for the user to click in the picker.
      if (
        (data.confidence === 'high' ||
          data.confidence === 'medium' ||
          data.confidence === 'user_confirmed') &&
        data.lat !== null &&
        data.lng !== null
      ) {
        setConfirmedBuilding({ lat: data.lat, lng: data.lng });
      }
    } catch {
      setBicResult({
        confidence: 'none',
        needs_user_confirmation: true,
        lat: null,
        lng: null,
        address: null,
        source: 'unresolved',
        source_chain: [],
        candidates: [],
        cached: false,
      });
    } finally {
      setBicLoading(false);
    }
  }

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
    setRun(null);
    setSubmitting(true);
    try {
      const auth = await authHeader();
      // The API treats `inbox_id` as optional (None = legacy round-robin),
      // but our form always carries an empty string when no sender is
      // configured for the tenant. Strip the empty value so the request
      // body matches the Pydantic optional contract instead of failing
      // the UUID coerce on the server.
      const { inbox_id: pickedInboxId, ...rest } = form;
      const payload: Record<string, unknown> = { ...rest };
      if (pickedInboxId) payload.inbox_id = pickedInboxId;
      // When the BIC has resolved a building (auto-high or user click)
      // we pass the coords so /test-pipeline can short-circuit the
      // cascade. The backend ignores these fields when null.
      if (confirmedBuilding) {
        payload.confirmed_building_lat = confirmedBuilding.lat;
        payload.confirmed_building_lng = confirmedBuilding.lng;
      }
      const res = await fetch(`${API_URL}/v1/demo/test-pipeline`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...auth },
        body: JSON.stringify(payload),
      });
      const data = (await res.json().catch(() => null)) as
        | {
            lead_id?: string;
            run_id?: string;
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
      setAttemptsAfterRun(data.attempts_remaining ?? 0);
      // Seed the polling cycle. The endpoint already flipped status to
      // 'creative' after scoring; the polling effect picks it up and
      // refreshes every 2s until done/failed.
      setRun({
        id: data.run_id ?? '',
        lead_id: data.lead_id,
        status: 'creative',
        failed_step: null,
        error_message: null,
        notes: null,
        updated_at: new Date().toISOString(),
        email_status: null,
        email_status_detail: null,
        email_recipient: null,
        email_message_id: null,
        roof_source: null,
        roof_confidence: null,
      });
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
          // Reset state on dismiss so the next open starts on the form.
          setRun(null);
          setAttemptsAfterRun(null);
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
                continuano in background (2-10 min, visibili live nella scheda
                del lead e in <code>/admin/demo-runs</code>).
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

          {run && run.status === 'failed' ? (
            <FailurePanel
              run={run}
              attemptsRemaining={attemptsAfterRun ?? attemptsRemaining}
              onRetry={() => {
                setRun(null);
                setAttemptsAfterRun(null);
              }}
              onClose={close}
            />
          ) : run && run.status === 'done' ? (
            <SuccessPanel
              leadId={run.lead_id ?? ''}
              attemptsRemaining={attemptsAfterRun ?? 0}
              notes={run.notes}
              emailStatus={run.email_status}
              emailStatusDetail={run.email_status_detail}
              emailRecipient={run.email_recipient}
              onClose={close}
            />
          ) : run ? (
            <ProgressPanel run={run} />
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

              {/* Building Identification Cascade — manual trigger.
                  We don't auto-fire on blur because the cascade is
                  expensive (Vision API ~$0.025) and hits multiple
                  external services in series. Once clicked, the result
                  is cached server-side per-VAT so subsequent runs are
                  instant. */}
              <BicSection
                bicResult={bicResult}
                bicLoading={bicLoading}
                confirmedBuilding={confirmedBuilding}
                vatNumber={form.vat_number}
                onIdentify={handleIdentifyBuilding}
                onConfirmed={(lat, lng) => {
                  setConfirmedBuilding({ lat, lng });
                  // Bump the result to user_confirmed so the badge
                  // turns green even though we don't refetch.
                  setBicResult((prev) =>
                    prev
                      ? { ...prev, confidence: 'user_confirmed', lat, lng }
                      : prev,
                  );
                }}
              />


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
                label="Mittente (inbox da cui parte l&apos;email)"
                required={inboxes.length > 0}
                helper={
                  inboxesLoading
                    ? 'Carico le inbox configurate…'
                    : inboxes.length === 0
                      ? 'Nessuna inbox configurata per il tenant — verrà usato il mittente di default.'
                      : 'L&apos;email arriverà al destinatario con questo From / DKIM.'
                }
              >
                <select
                  required={inboxes.length > 0}
                  disabled={inboxesLoading || inboxes.length === 0}
                  value={form.inbox_id}
                  onChange={(e) =>
                    setForm({ ...form, inbox_id: e.target.value })
                  }
                  className={inputClass}
                >
                  {inboxes.length === 0 && (
                    <option value="">— nessuna inbox disponibile —</option>
                  )}
                  {inboxes.map((ib) => (
                    <option key={ib.id} value={ib.id}>
                      {ib.display_name
                        ? `${ib.display_name} <${ib.email}>`
                        : ib.email}
                    </option>
                  ))}
                </select>
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
                      Scoring…
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
  notes,
  emailStatus,
  emailStatusDetail,
  emailRecipient,
  onClose,
}: {
  leadId: string;
  attemptsRemaining: number;
  notes?: string | null;
  emailStatus: EmailStatus | null;
  emailStatusDetail: string | null;
  emailRecipient: string | null;
  onClose: () => void;
}) {
  // Pick the panel tone + copy from the *real* delivery state, not just
  // "pipeline is done". The previous behaviour celebrated success the
  // moment the API queued the send, hiding silent bounces and the
  // ``DEMO_EMAIL_RECIPIENT_OVERRIDE`` redirect from operators.
  //
  // Mapping:
  //   DELIVERED/OPENED/CLICKED → success (green)
  //   FAILED                   → error   (red, surface bounce reason)
  //   SENT (no webhook yet)    → pending (amber, ask user to wait/check)
  //   null  (still in flight)  → pending — same copy
  const isDelivered =
    emailStatus === 'DELIVERED' ||
    emailStatus === 'OPENED' ||
    emailStatus === 'CLICKED';
  const isFailed = emailStatus === 'FAILED';
  const isPending = !isDelivered && !isFailed; // SENT or null

  // Tone: green for delivered, red for failed, amber for pending.
  const tone = isDelivered
    ? {
        bg: 'bg-primary/5 ring-primary/15',
        title: 'text-primary',
        heading: 'Email consegnata 🎉',
      }
    : isFailed
      ? {
          bg: 'bg-rose-500/5 ring-rose-500/20',
          title: 'text-rose-300',
          heading: 'Email non recapitata',
        }
      : {
          bg: 'bg-amber-500/5 ring-amber-500/20',
          title: 'text-amber-300',
          heading: 'Email accettata · in attesa di consegna',
        };

  return (
    <div className={cn('space-y-3 rounded-xl p-4 ring-1', tone.bg)}>
      <p className={cn('font-headline text-lg font-bold', tone.title)}>
        {tone.heading}
      </p>
      <p className="text-sm text-on-surface">
        {isDelivered ? (
          <>
            Il lead è stato creato, il tetto renderizzato e l&apos;email
            consegnata{emailRecipient ? ` a ${emailRecipient}` : ''}. Apri
            la scheda del lead per vedere gli eventi di tracking in tempo
            reale (apertura, click, visita pagina personale).
          </>
        ) : isFailed ? (
          <>
            Resend ha rifiutato la consegna
            {emailRecipient ? ` verso ${emailRecipient}` : ''}
            {emailStatusDetail ? ` (${emailStatusDetail})` : ''}. Verifica
            l&apos;indirizzo del destinatario e lo stato del dominio
            mittente in Resend (DKIM/SPF/MX su <code>agenda-pro.it</code>).
          </>
        ) : (
          <>
            Resend ha accettato la richiesta di invio
            {emailRecipient ? ` (destinatario: ${emailRecipient})` : ''}
            ma non abbiamo ancora ricevuto la conferma di consegna. La
            conferma arriva via webhook entro 1-2 minuti; se questo box
            resta giallo, il webhook non è registrato o il dominio
            mittente non è verificato in Resend.
          </>
        )}
      </p>
      {notes && (
        <p className="rounded-lg bg-warning-container/50 px-3 py-2 text-xs text-on-warning-container">
          {notes}
        </p>
      )}
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
          className={cn(
            'inline-flex items-center gap-1.5 rounded-full px-4 py-2 text-xs font-semibold shadow-ambient-sm hover:shadow-ambient-md',
            isFailed
              ? 'bg-rose-500 text-white'
              : isDelivered
                ? 'bg-primary text-on-primary'
                : 'bg-amber-500 text-white',
          )}
        >
          Vai al lead
          <ArrowRight size={14} strokeWidth={2.5} />
        </a>
      </div>
    </div>
  );
}

// Live "scoring → creative → outreach → done" indicator that mirrors
// the server-side state machine. We render every step with a check /
// spinner / dot so the user can see exactly where the pipeline is.
function ProgressPanel({ run }: { run: RunSnapshot }) {
  const steps: Array<{ key: RunStatus; label: string }> = [
    { key: 'scoring', label: 'Scoring lead' },
    { key: 'creative', label: 'Rendering tetto' },
    { key: 'outreach', label: 'Invio email' },
  ];
  const currentIdx = steps.findIndex((s) => s.key === run.status);
  return (
    <div className="space-y-3 rounded-xl bg-surface-container-low p-4">
      <p className="font-headline text-lg font-bold text-on-surface">
        Pipeline in corso…
      </p>
      <p className="text-xs text-on-surface-variant">
        Lead creato. Rendering tetto 2-5 min · video Kling fino a 5 min
        aggiuntivi nei picchi di coda Replicate · invio email ~5s. Puoi
        chiudere questa finestra: il pipeline continua in background, lo
        stato live è su <code>/admin/demo-runs</code> e nella scheda del lead.
      </p>
      <ul className="space-y-2">
        {steps.map((step, idx) => {
          const done = currentIdx > idx || run.status === 'done';
          const active = currentIdx === idx && run.status !== 'done';
          return (
            <li
              key={step.key}
              className="flex items-center gap-3 text-sm"
            >
              <span
                className={
                  done
                    ? 'flex h-6 w-6 items-center justify-center rounded-full bg-primary text-on-primary'
                    : active
                      ? 'flex h-6 w-6 items-center justify-center rounded-full bg-primary/15 text-primary'
                      : 'flex h-6 w-6 items-center justify-center rounded-full bg-surface-container-high text-on-surface-variant'
                }
              >
                {done ? (
                  '✓'
                ) : active ? (
                  <Loader2
                    size={12}
                    strokeWidth={2.75}
                    className="animate-spin"
                  />
                ) : (
                  <span className="text-[10px]">{idx + 1}</span>
                )}
              </span>
              <span
                className={
                  done || active
                    ? 'text-on-surface'
                    : 'text-on-surface-variant'
                }
              >
                {step.label}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// Hard-fail panel — the run terminated in 'failed' state. Server has
// already refunded the attempt counter, so the user can immediately
// retry from a fresh form. We surface the actual error_message so we
// don't have to ship a debugger every time something breaks.
function FailurePanel({
  run,
  attemptsRemaining,
  onRetry,
  onClose,
}: {
  run: RunSnapshot;
  attemptsRemaining: number;
  onRetry: () => void;
  onClose: () => void;
}) {
  const stepLabel =
    run.failed_step === 'scoring'
      ? 'durante lo scoring'
      : run.failed_step === 'creative'
        ? 'durante il rendering del tetto'
        : run.failed_step === 'outreach'
          ? "durante l'invio dell'email"
          : '';
  return (
    <div className="space-y-3 rounded-xl bg-error-container/30 p-4 ring-1 ring-error/30">
      <div className="flex items-start gap-2">
        <AlertTriangle
          size={20}
          strokeWidth={2.25}
          className="mt-0.5 text-error"
          aria-hidden
        />
        <div>
          <p className="font-headline text-base font-bold text-error">
            Pipeline interrotta {stepLabel}
          </p>
          {run.error_message && (
            <p className="mt-1 break-words text-xs text-on-error-container">
              {run.error_message}
            </p>
          )}
        </div>
      </div>
      <p className="text-xs text-on-surface-variant">
        Il tentativo è stato rimborsato. Tentativi rimanenti:{' '}
        <strong>{attemptsRemaining}/3</strong>.
      </p>
      <div className="flex items-center justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onClose}
          className="rounded-full px-3 py-1.5 text-xs font-semibold text-on-surface-variant hover:bg-surface-container-high"
        >
          Chiudi
        </button>
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-xs font-semibold text-on-primary shadow-ambient-sm hover:shadow-ambient-md"
        >
          Riprova
          <ArrowRight size={14} strokeWidth={2.5} />
        </button>
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

// ---------------------------------------------------------------------------
// BicSection — the "Identifica capannone" panel inside the form.
//
// Three states:
//   1. Idle (no result yet)        → show "Identifica capannone" button.
//   2. Loading                     → show spinner + "Sto cercando il capannone…".
//   3. Resolved with high/medium   → show green badge + summary.
//   4. Resolved with low/none      → show <BuildingPicker/> inline (the user
//      MUST click a candidate before we let the form submit).
// ---------------------------------------------------------------------------

function BicSection({
  bicResult,
  bicLoading,
  confirmedBuilding,
  vatNumber,
  onIdentify,
  onConfirmed,
}: {
  bicResult: BicResult | null;
  bicLoading: boolean;
  confirmedBuilding: { lat: number; lng: number } | null;
  vatNumber: string;
  onIdentify: () => void;
  onConfirmed: (lat: number, lng: number) => void;
}) {
  // Idle — show the trigger button only.
  if (!bicResult && !bicLoading) {
    return (
      <div className="rounded-lg bg-surface-container-low p-3 ring-1 ring-on-surface/10">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold text-on-surface">
              Identifica capannone
            </p>
            <p className="text-[11px] text-on-surface-variant">
              Avvia la cascade (Atoka + Places + OSM + Vision) per
              localizzare l&apos;edificio prima del render. Costo ~ €0,15
              quando arriva fino alla vision; risultato cachato per VAT.
            </p>
          </div>
          <button
            type="button"
            onClick={onIdentify}
            className="inline-flex items-center gap-1.5 rounded-full bg-secondary-container px-3 py-1.5 text-xs font-semibold text-on-secondary-container hover:shadow-ambient-sm"
          >
            <Building size={13} strokeWidth={2.5} />
            Identifica capannone
          </button>
        </div>
      </div>
    );
  }

  if (bicLoading) {
    return (
      <div className="flex items-center gap-2 rounded-lg bg-surface-container-low p-3 ring-1 ring-on-surface/10">
        <Loader2 size={14} strokeWidth={2.5} className="animate-spin text-primary" />
        <p className="text-xs text-on-surface">
          Sto cercando il capannone — Atoka → Places → OSM → Vision…
          (può richiedere fino a 30s)
        </p>
      </div>
    );
  }

  if (!bicResult) return null;

  const isHigh = bicResult.confidence === 'high';
  const isMedium = bicResult.confidence === 'medium';
  const isUserConfirmed = bicResult.confidence === 'user_confirmed' || !!confirmedBuilding;
  const isLow = bicResult.confidence === 'low';
  const isNone = bicResult.confidence === 'none';

  // High / medium / user_confirmed → show summary badge only.
  if (isHigh || isMedium || isUserConfirmed) {
    const tone = isUserConfirmed || isHigh ? 'primary' : 'secondary';
    return (
      <div
        className={cn(
          'flex items-start gap-2 rounded-lg p-3 ring-1',
          tone === 'primary'
            ? 'bg-primary/5 ring-primary/30'
            : 'bg-secondary-container ring-secondary/30',
        )}
      >
        <Check
          size={16}
          strokeWidth={2.5}
          className={cn(
            'mt-0.5 shrink-0',
            tone === 'primary' ? 'text-primary' : 'text-on-secondary-container',
          )}
        />
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-on-surface">
            {isUserConfirmed
              ? 'Capannone confermato dall’utente'
              : isHigh
                ? 'Capannone identificato (alta confidenza)'
                : 'Capannone identificato (media confidenza)'}
          </p>
          <p className="text-[11px] text-on-surface-variant">
            Sorgente: {bicResult.source}
            {bicResult.cached ? ' · da cache' : ''}
            {bicResult.lat !== null && bicResult.lng !== null
              ? ` · ${bicResult.lat.toFixed(5)}, ${bicResult.lng.toFixed(5)}`
              : ''}
          </p>
          {bicResult.address && (
            <p className="mt-0.5 truncate text-[11px] text-on-surface">
              {bicResult.address}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={onIdentify}
          className="text-[10px] font-semibold text-on-surface-variant hover:text-on-surface"
        >
          Re-identifica
        </button>
      </div>
    );
  }

  // Low / none → show the picker inline. The user MUST click a candidate
  // (or freehand) before the form will submit — the form's submit button
  // is left enabled but /test-pipeline will re-run the cascade if the
  // user submits anyway, so the worst case is a slow second cascade.
  return (
    <div className="space-y-2">
      <div className="flex items-start gap-2 rounded-lg bg-warning-container/40 p-3 ring-1 ring-warning/30">
        <AlertTriangle size={16} strokeWidth={2.5} className="mt-0.5 shrink-0 text-warning" />
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-on-warning-container">
            {isLow
              ? 'Capannone identificato con bassa confidenza'
              : 'Non sono riuscito a identificare il capannone automaticamente'}
          </p>
          <p className="text-[11px] text-on-warning-container/80">
            Conferma cliccando il candidato giusto sulla mappa. Il pick
            verrà salvato per la P.IVA, quindi al prossimo run il render
            partirà subito senza chiedere di nuovo.
          </p>
        </div>
      </div>
      <BuildingPicker
        vatNumber={vatNumber}
        candidates={bicResult.candidates}
        fallbackCentre={
          bicResult.lat !== null && bicResult.lng !== null
            ? { lat: bicResult.lat, lng: bicResult.lng }
            : undefined
        }
        onConfirmed={onConfirmed}
      />
    </div>
  );
}
