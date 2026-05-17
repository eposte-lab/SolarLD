/**
 * Settings → Template follow-up — editor dei 4 template del
 * compositore "Scrivi follow-up" (Recap ROI, Riattivazione, Invito al
 * sopralluogo, Dopo il sopralluogo).
 *
 * `label`/`description` sono fissi (descrivono QUANDO usare ogni
 * template). L'operatore modifica solo oggetto + corpo; gli override
 * vivono su `tenants.followup_templates` (migration 0134) e valgono
 * per tutto il tenant. Un template lasciato uguale al default non
 * cambia nulla — `mergeFollowupTemplates` riapplica il default per i
 * campi vuoti.
 *
 * Pattern: client component, GET /v1/tenants/me al mount, PATCH al
 * submit (il route /v1/tenants/me whitelist-a `followup_templates`).
 */
'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { ArrowLeft, Check, Loader2, RotateCcw } from 'lucide-react';

import { api, ApiError } from '@/lib/api-client';
import {
  FOLLOWUP_TEMPLATE_DEFAULTS,
  type FollowupTemplateOverrides,
} from '@/lib/followup-templates';

type Draft = Record<string, { subject: string; body: string }>;

/** I segnaposto risolti automaticamente all'invio. */
const PLACEHOLDERS =
  '{{nome}} · {{azienda}} · {{comune}} · {{kwp}} · {{risparmio}} · ' +
  '{{risparmio_annuo_minimo}} · {{payback}} · {{firma}}';

function draftFromOverrides(ov: FollowupTemplateOverrides | null): Draft {
  const d: Draft = {};
  for (const t of FOLLOWUP_TEMPLATE_DEFAULTS) {
    const o = ov?.[t.id];
    d[t.id] = {
      subject: o?.subject?.trim() ? o.subject : t.subject,
      body: o?.body?.trim() ? o.body : t.body,
    };
  }
  return d;
}

export default function SettingsFollowupTemplatesPage() {
  const [draft, setDraft] = useState<Draft>(() => draftFromOverrides(null));
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
        const ov = (row.followup_templates ?? null) as FollowupTemplateOverrides | null;
        setDraft(draftFromOverrides(ov));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError ? err.message : 'Errore caricamento template.',
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
      await api.patch('/v1/tenants/me', { followup_templates: draft });
      setSavedAt(Date.now());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Salvataggio fallito.');
    } finally {
      setSubmitting(false);
    }
  }

  const setField = (id: string, k: 'subject' | 'body') => (v: string) =>
    setDraft((d) => ({ ...d, [id]: { ...d[id]!, [k]: v } }));

  const resetOne = (id: string) => {
    const def = FOLLOWUP_TEMPLATE_DEFAULTS.find((t) => t.id === id);
    if (!def) return;
    setDraft((d) => ({ ...d, [id]: { subject: def.subject, body: def.body } }));
  };

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
          Impostazioni · Template follow-up
        </p>
        <h1 className="mt-1 font-headline text-3xl font-bold tracking-tighter text-on-surface">
          Template del compositore follow-up
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
          Sono i 4 testi precompilati che l&apos;operatore trova nel modulo
          &quot;Scrivi follow-up&quot; di ogni lead. Le modifiche valgono per
          tutto il team. I segnaposto vengono sostituiti coi dati del lead
          all&apos;invio:
        </p>
        <p className="mt-1 font-mono text-[11px] text-on-surface-variant">
          {PLACEHOLDERS}
        </p>
      </header>

      {loading ? (
        <div className="flex items-center gap-2 py-8 text-sm text-on-surface-variant">
          <Loader2 size={16} className="animate-spin" /> Caricamento…
        </div>
      ) : (
        <form onSubmit={onSubmit} className="space-y-4">
          {FOLLOWUP_TEMPLATE_DEFAULTS.map((t) => (
            <fieldset
              key={t.id}
              className="space-y-3 rounded-xl border border-outline-variant bg-surface-container px-5 py-4"
            >
              <legend className="flex items-center gap-2 px-2">
                <span className="text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                  {t.label}
                </span>
              </legend>
              <p className="text-xs text-on-surface-variant">{t.description}</p>

              <label className="flex flex-col gap-1 text-xs">
                <span className="text-on-surface-variant">Oggetto</span>
                <input
                  type="text"
                  value={draft[t.id]?.subject ?? ''}
                  onChange={(e) => setField(t.id, 'subject')(e.target.value)}
                  className="rounded-md border border-outline-variant bg-surface px-3 py-2 text-sm text-on-surface focus:border-primary focus:outline-none"
                />
              </label>

              <label className="flex flex-col gap-1 text-xs">
                <span className="text-on-surface-variant">Corpo email</span>
                <textarea
                  rows={12}
                  value={draft[t.id]?.body ?? ''}
                  onChange={(e) => setField(t.id, 'body')(e.target.value)}
                  className="rounded-md border border-outline-variant bg-surface px-3 py-2 text-sm leading-relaxed text-on-surface focus:border-primary focus:outline-none"
                />
              </label>

              <button
                type="button"
                onClick={() => resetOne(t.id)}
                className="inline-flex items-center gap-1 text-xs font-medium text-on-surface-variant hover:text-primary"
              >
                <RotateCcw size={12} /> Ripristina testo predefinito
              </button>
            </fieldset>
          ))}

          <div className="flex items-center justify-end gap-3 pt-2">
            {error && <span className="text-xs text-rose-700">{error}</span>}
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
                <>Salva template</>
              )}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
