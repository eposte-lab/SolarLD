'use client';

/**
 * PipelineTestPanel — admin-only end-to-end pipeline smoke test.
 *
 * Pre-fills GALLO GAETANO test data. Calls POST /v1/admin/seed-test-candidate
 * using the existing browser session (no manual JWT needed).
 *
 * Timeline after submit:
 *   t+0s   scoring  — creates leads row, sets score/tier
 *   t+45s  creative — Remotion renders MP4+GIF, uploads to storage
 *   t+3min outreach — sends real email via Resend on verified domain
 */

import { useState } from 'react';

import { api, API_URL } from '@/lib/api-client';

interface SeedResponse {
  ok: boolean;
  roof_id: string;
  subject_id: string;
  scoring_job_id: string;
  creative_job_id: string;
  outreach_job_id: string | null;
  outreach_result: string | null;
  message: string;
}

const DEFAULT_FORM = {
  vat_number: 'IT06662831210',
  legal_name: 'GALLO GAETANO',
  ateco_code: '43.21.01',
  hq_address: 'Viale dei Pini Nord 14, 80014 Giugliano in Campania NA',
  hq_cap: '80014',
  hq_city: 'Giugliano in Campania',
  hq_province: 'NA',
  // Coords approx Viale dei Pini Nord, Giugliano — refine in the form if needed.
  hq_lat: '40.9520',
  hq_lng: '14.0890',
  decision_maker_name: 'Gaetano Gallo',
  decision_maker_role: 'Titolare',
  decision_maker_email: 'wisp.rent@gmail.com',
  run_outreach: true,
};

interface Props {
  tenantId: string;
}

export function PipelineTestPanel({ tenantId }: Props) {
  const [form, setForm] = useState(DEFAULT_FORM);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<SeedResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<'idle' | 'scoring' | 'creative' | 'outreach' | 'done'>('idle');

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    setPhase('scoring');

    try {
      // Sanity check: if API_URL is localhost we're in a broken Vercel env
      if (typeof window !== 'undefined' && API_URL.includes('localhost') && !window.location.hostname.includes('localhost')) {
        throw new Error(
          'NEXT_PUBLIC_API_URL punta a localhost — imposta la variabile d\'ambiente su Vercel con l\'URL del tuo Railway API (es. https://api-xxx.up.railway.app).'
        );
      }

      const res = await api.post<SeedResponse>('/v1/admin/seed-test-candidate', {
        tenant_id: tenantId,
        vat_number: form.vat_number,
        legal_name: form.legal_name,
        ateco_code: form.ateco_code || null,
        hq_address: form.hq_address,
        hq_cap: form.hq_cap,
        hq_city: form.hq_city,
        hq_province: form.hq_province,
        hq_lat: parseFloat(form.hq_lat),
        hq_lng: parseFloat(form.hq_lng),
        decision_maker_name: form.decision_maker_name || null,
        decision_maker_role: form.decision_maker_role || null,
        decision_maker_email: form.decision_maker_email || null,
        run_outreach: form.run_outreach,
      });

      setResult(res);
      setPhase('done');
    } catch (e: unknown) {
      const err = e as { message?: string; body?: { detail?: string } };
      const rawMsg = err?.body?.detail ?? err?.message ?? 'Errore sconosciuto';
      // "Failed to fetch" / "NetworkError" = CORS o URL sbagliato
      const isNetworkError =
        rawMsg.toLowerCase().includes('failed to fetch') ||
        rawMsg.toLowerCase().includes('network') ||
        rawMsg.toLowerCase().includes('networkerror');
      setError(
        isNetworkError
          ? `Errore di rete — impossibile raggiungere l'API.\n\nURL API: ${API_URL}\n\nCause probabili:\n1. NEXT_PUBLIC_API_URL non impostato su Vercel (punta a localhost)\n2. CORS: il tuo dominio non è nella lista origini API\n3. L'API Railway è spenta\n\nErrore originale: ${rawMsg}`
          : rawMsg
      );
      setPhase('idle');
    } finally {
      setLoading(false);
    }
  }

  function Field({
    label,
    name,
    type = 'text',
    readOnly = false,
  }: {
    label: string;
    name: keyof typeof DEFAULT_FORM;
    type?: string;
    readOnly?: boolean;
  }) {
    return (
      <div>
        <label className="block text-xs font-semibold text-on-surface-variant">{label}</label>
        <input
          type={type}
          value={form[name] as string}
          readOnly={readOnly}
          onChange={(e) => setForm((f) => ({ ...f, [name]: e.target.value }))}
          className="mt-1 w-full rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-3 py-1.5 font-mono text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary/60 read-only:opacity-60"
        />
      </div>
    );
  }

  const apiIsLocalhost = API_URL.includes('localhost') || API_URL.includes('127.0.0.1');

  return (
    <div className="space-y-6">
      {/* API URL debug strip */}
      <div className={`flex items-center gap-2 rounded-lg px-3 py-2 text-xs ${
        apiIsLocalhost
          ? 'border border-error/30 bg-error-container/20 text-error'
          : 'border border-primary/20 bg-primary-container/10 text-on-surface-variant'
      }`}>
        <span className="font-mono">{API_URL}</span>
        {apiIsLocalhost && (
          <span className="font-semibold">
            ⚠️ localhost — imposta NEXT_PUBLIC_API_URL su Vercel
          </span>
        )}
      </div>

      <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
        Dati candidato di test
      </p>

      <form onSubmit={handleSubmit} className="space-y-5">
        {/* Azienda */}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="P.IVA" name="vat_number" />
          <Field label="Ragione sociale" name="legal_name" />
          <Field label="ATECO" name="ateco_code" />
        </div>

        {/* Indirizzo */}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="sm:col-span-2">
            <Field label="Indirizzo HQ" name="hq_address" />
          </div>
          <Field label="CAP" name="hq_cap" />
          <Field label="Provincia" name="hq_province" />
          <Field label="Città" name="hq_city" />
          <Field label="Latitudine" name="hq_lat" />
          <Field label="Longitudine" name="hq_lng" />
        </div>

        {/* Decision maker */}
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Nome decision maker" name="decision_maker_name" />
          <Field label="Ruolo" name="decision_maker_role" />
          <div>
            <label className="block text-xs font-semibold text-on-surface-variant">
              Email destinatario (riceverà la mail reale)
            </label>
            <input
              type="email"
              value={form.decision_maker_email}
              onChange={(e) => setForm((f) => ({ ...f, decision_maker_email: e.target.value }))}
              className="mt-1 w-full rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-3 py-1.5 text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary/60"
            />
          </div>
        </div>

        {/* Outreach toggle */}
        <label className="flex cursor-pointer items-center gap-3">
          <input
            type="checkbox"
            checked={form.run_outreach}
            onChange={(e) => setForm((f) => ({ ...f, run_outreach: e.target.checked }))}
            className="h-4 w-4 rounded border-outline-variant/40 accent-primary"
          />
          <span className="text-sm text-on-surface">
            Invia email reale (sincrono — nello stesso request)
          </span>
        </label>

        {/* Submit */}
        <button
          type="submit"
          disabled={loading}
          className="rounded-lg bg-primary px-6 py-2.5 text-sm font-semibold text-on-primary transition-opacity disabled:opacity-50 hover:opacity-90"
        >
          {loading ? '⏳ Pipeline in corso…' : '🚀 Avvia test pipeline'}
        </button>
      </form>

      {/* Progress indicator */}
      {phase !== 'idle' && phase !== 'done' && (
        <div className="flex items-center gap-3 rounded-xl border border-primary/20 bg-primary-container/10 px-4 py-3">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <span className="text-sm font-medium text-primary">
            {phase === 'scoring' && 'Scoring in corso…'}
            {phase === 'creative' && 'Rendering Remotion in coda (~45s)…'}
            {phase === 'outreach' && 'Email in coda (~3 min)…'}
          </span>
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="space-y-3 rounded-xl border border-primary/20 bg-primary-container/10 p-4">
          <p className="font-semibold text-primary">✓ Pipeline avviata</p>
          <p className="text-sm text-on-surface">{result.message}</p>

          {/* Outreach result badge */}
          {result.outreach_result && (
            <div className={`rounded-lg px-3 py-2 text-sm font-medium ${
              result.outreach_result.startsWith('sent')
                ? 'bg-green-500/10 text-green-700 border border-green-500/20'
                : 'bg-amber-500/10 text-amber-700 border border-amber-500/20'
            }`}>
              {result.outreach_result.startsWith('sent') ? '✉️ ' : '⚠️ '}
              {result.outreach_result}
            </div>
          )}

          <div className="grid gap-2 rounded-lg bg-surface-container-lowest p-3 font-mono text-xs text-on-surface-variant sm:grid-cols-2">
            <span>roof_id: <span className="text-on-surface">{result.roof_id}</span></span>
            <span>subject_id: <span className="text-on-surface">{result.subject_id}</span></span>
            <span>scoring: <span className="text-on-surface">{result.scoring_job_id}</span></span>
            <span>creative: <span className="text-on-surface">{result.creative_job_id}</span></span>
            {result.outreach_job_id && (
              <span>outreach: <span className="text-on-surface">{result.outreach_job_id}</span></span>
            )}
          </div>
          <p className="text-xs text-on-surface-variant">
            {result.outreach_result?.startsWith('sent')
              ? <>Email inviata — controlla la casella <strong>{form.decision_maker_email}</strong> ora.</>
              : <>Controlla <strong>Lead Attivi</strong> in dashboard.</>
            }
          </p>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-xl border border-error/30 bg-error-container/20 p-4">
          <p className="text-sm font-semibold text-error">Errore</p>
          <pre className="mt-1 whitespace-pre-wrap break-words text-xs text-on-error-container font-mono">
            {error}
          </pre>
          {error.includes('super_admin') && (
            <p className="mt-2 text-xs text-on-surface-variant">
              Il tuo account non ha il ruolo <code>super_admin</code>. Contatta l&apos;ops per aggiornarlo.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
