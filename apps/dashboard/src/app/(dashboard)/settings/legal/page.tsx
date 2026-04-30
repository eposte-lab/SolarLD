/**
 * Settings → Dati legali — i 7 (+3 GDPR) campi del tenant richiesti
 * dalla generazione delle pratiche GSE.
 *
 * Il DM 37/08 (dichiarazione di conformità impianto elettrico) richiede
 * codice fiscale impresa, numero CCIAA, e dati del responsabile tecnico
 * (nome, cognome, CF, qualifica, iscrizione albo). Senza questi campi
 * il worker fallisce la validazione e marca il documento `error` —
 * compilare qui evita il round-trip "crea pratica → fallisce → torna a
 * Settings".
 *
 * Pattern: client component, GET /v1/tenants/me al mount, PATCH al
 * submit. Niente server actions perché il payload è un singolo PATCH
 * verso il backend FastAPI (il route /v1/tenants/me già whitelist-a
 * questi 10 campi).
 */
'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { ArrowLeft, Check, Loader2 } from 'lucide-react';

import { api, ApiError } from '@/lib/api-client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

// Loose typing — the tenant row has many other fields we don't touch.
interface TenantPartial {
  legal_name: string | null;
  legal_address: string | null;
  vat_number: string | null;
  codice_fiscale: string | null;
  numero_cciaa: string | null;
  responsabile_tecnico_nome: string | null;
  responsabile_tecnico_cognome: string | null;
  responsabile_tecnico_codice_fiscale: string | null;
  responsabile_tecnico_qualifica: string | null;
  responsabile_tecnico_iscrizione_albo: string | null;
}

const FIELDS: Array<keyof TenantPartial> = [
  'legal_name',
  'legal_address',
  'vat_number',
  'codice_fiscale',
  'numero_cciaa',
  'responsabile_tecnico_nome',
  'responsabile_tecnico_cognome',
  'responsabile_tecnico_codice_fiscale',
  'responsabile_tecnico_qualifica',
  'responsabile_tecnico_iscrizione_albo',
];

const EMPTY: TenantPartial = {
  legal_name: '',
  legal_address: '',
  vat_number: '',
  codice_fiscale: '',
  numero_cciaa: '',
  responsabile_tecnico_nome: '',
  responsabile_tecnico_cognome: '',
  responsabile_tecnico_codice_fiscale: '',
  responsabile_tecnico_qualifica: '',
  responsabile_tecnico_iscrizione_albo: '',
};

// ---------------------------------------------------------------------------

export default function SettingsLegalPage() {
  const [form, setForm] = useState<TenantPartial>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .get<Record<string, unknown>>('/v1/tenants/me')
      .then((row) => {
        if (cancelled) return;
        // Hydrate only the keys we care about; null → "" so React inputs stay
        // controlled (otherwise switching null↔string triggers a warning).
        const next: TenantPartial = { ...EMPTY };
        for (const k of FIELDS) {
          const raw = row[k];
          if (typeof raw === 'string') next[k] = raw;
        }
        setForm(next);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError
            ? err.message
            : 'Errore caricamento dati legali.',
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      // Send all 10 fields. The backend allowlist drops anything else
      // even if the form ever grows, so this stays safe.
      await api.patch('/v1/tenants/me', form);
      setSavedAt(Date.now());
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'Salvataggio fallito.',
      );
    } finally {
      setSubmitting(false);
    }
  }

  const update = (k: keyof TenantPartial) => (v: string) =>
    setForm((f) => ({ ...f, [k]: v }));

  return (
    <div className="space-y-6">
      <Link
        href="/settings"
        className="inline-flex items-center gap-1 text-xs font-medium text-on-surface-variant hover:text-primary"
      >
        <ArrowLeft size={12} /> Torna a Impostazioni
      </Link>

      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Impostazioni · Dati legali
        </p>
        <h1 className="mt-1 font-headline text-3xl font-bold tracking-tighter text-on-surface">
          Anagrafica e responsabile tecnico
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
          Questi dati vengono inseriti automaticamente nelle pratiche GSE che
          generi: dichiarazione di conformità DM 37/08, comunicazioni al
          Comune, Modello Unico GSE. Compila tutti i campi prima di creare la
          prima pratica per evitare errori di validazione.
        </p>
      </header>

      {loading ? (
        <div className="flex items-center gap-2 py-8 text-sm text-on-surface-variant">
          <Loader2 size={16} className="animate-spin" /> Caricamento…
        </div>
      ) : (
        <form onSubmit={onSubmit} className="space-y-4">
          <Section title="Anagrafica impresa">
            <Field label="Ragione sociale (legale)" wide>
              <Input
                value={form.legal_name ?? ''}
                onChange={update('legal_name')}
              />
            </Field>
            <Field label="Sede legale (indirizzo completo)" wide>
              <Input
                value={form.legal_address ?? ''}
                onChange={update('legal_address')}
              />
            </Field>
            <Field label="Partita IVA">
              <Input
                value={form.vat_number ?? ''}
                onChange={update('vat_number')}
              />
            </Field>
            <Field label="Codice fiscale impresa">
              <Input
                value={form.codice_fiscale ?? ''}
                onChange={update('codice_fiscale')}
              />
            </Field>
            <Field label="Numero CCIAA (es. MI-1234567)">
              <Input
                value={form.numero_cciaa ?? ''}
                onChange={update('numero_cciaa')}
              />
            </Field>
          </Section>

          <Section title="Responsabile tecnico">
            <Field label="Nome">
              <Input
                value={form.responsabile_tecnico_nome ?? ''}
                onChange={update('responsabile_tecnico_nome')}
              />
            </Field>
            <Field label="Cognome">
              <Input
                value={form.responsabile_tecnico_cognome ?? ''}
                onChange={update('responsabile_tecnico_cognome')}
              />
            </Field>
            <Field label="Codice fiscale">
              <Input
                value={form.responsabile_tecnico_codice_fiscale ?? ''}
                onChange={update('responsabile_tecnico_codice_fiscale')}
              />
            </Field>
            <Field label="Qualifica (es. Ingegnere, Perito industriale)">
              <Input
                value={form.responsabile_tecnico_qualifica ?? ''}
                onChange={update('responsabile_tecnico_qualifica')}
              />
            </Field>
            <Field label="Iscrizione albo (es. Ordine Ingegneri Milano n. 1234)" wide>
              <Input
                value={form.responsabile_tecnico_iscrizione_albo ?? ''}
                onChange={update('responsabile_tecnico_iscrizione_albo')}
              />
            </Field>
          </Section>

          <div className="flex items-center justify-end gap-3 pt-2">
            {error && (
              <span className="text-xs text-rose-700">{error}</span>
            )}
            {savedAt && !submitting && (
              <span className="inline-flex items-center gap-1 text-xs text-emerald-700">
                <Check size={12} /> Salvato
              </span>
            )}
            <button
              type="submit"
              disabled={submitting}
              className="inline-flex items-center gap-2 rounded-full bg-gradient-primary px-6 py-3 text-sm font-bold text-on-primary shadow-ambient-sm transition-all hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? (
                <>
                  <Loader2 size={14} className="animate-spin" /> Salvataggio…
                </>
              ) : (
                <>Salva</>
              )}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tiny presentational primitives (mirror quote/page.tsx)
// ---------------------------------------------------------------------------

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <fieldset className="space-y-3 rounded-xl border border-outline-variant bg-surface-container px-5 py-4">
      <legend className="px-2 text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
        {title}
      </legend>
      <div className="grid gap-3 sm:grid-cols-2">{children}</div>
    </fieldset>
  );
}

function Field({
  label,
  children,
  wide,
}: {
  label: string;
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <label
      className={`flex flex-col gap-1 text-xs ${wide ? 'sm:col-span-2' : ''}`}
    >
      {label && <span className="text-on-surface-variant">{label}</span>}
      {children}
    </label>
  );
}

function Input({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-md border border-outline-variant bg-surface px-3 py-2 text-sm text-on-surface focus:border-primary focus:outline-none"
    />
  );
}
