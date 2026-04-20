'use client';

/**
 * BrandingEditor — live-preview email branding editor.
 *
 * Features:
 *  - Color picker, logo URL, email-from-name (saved via PATCH /v1/tenants/me)
 *  - Visual style picker: classic / bold / minimal
 *  - "🎨 Rigenera con AI" — calls POST /v1/branding/regenerate-email, fills
 *    copy overrides + sets recommended style, persisted server-side.
 *  - Live iframe preview (debounced 600 ms, fetches actual Jinja2 render)
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useTransition,
} from 'react';

import { createBrowserClient } from '@/lib/supabase/client';
import { cn } from '@/lib/utils';
import type { TenantRow, TenantSettings } from '@/types/db';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

type EmailStyle = 'classic' | 'bold' | 'minimal';

// ------------------------------------------------------------------ helpers

async function getAuthHeader(): Promise<Record<string, string>> {
  if (typeof window === 'undefined') return {};
  const sb = createBrowserClient();
  const {
    data: { session },
  } = await sb.auth.getSession();
  if (!session?.access_token) return {};
  return { Authorization: `Bearer ${session.access_token}` };
}

async function patchTenant(payload: Record<string, unknown>) {
  const auth = await getAuthHeader();
  const res = await fetch(`${API_URL}/v1/tenants/me`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...auth },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(
      (err as { detail?: string }).detail ?? `HTTP ${res.status}`,
    );
  }
}

function buildPreviewUrl(
  template: 'b2b' | 'b2c',
  color: string,
  tenantName: string,
  style: EmailStyle,
): string {
  const qs = new URLSearchParams({
    template,
    step: '1',
    color: color.replace('#', ''),
    tenant_name: tenantName,
    style,
  });
  return `${API_URL}/v1/branding/email-preview?${qs.toString()}`;
}

// ------------------------------------------------------------------ style config

const EMAIL_STYLES: Array<{
  id: EmailStyle;
  label: string;
  desc: string;
  preview: string; // emoji thumbnail
}> = [
  {
    id: 'classic',
    label: 'Classic',
    desc: 'Card bianca, barra colorata in cima, CTA arrotondato — professionale e rassicurante.',
    preview: '▬',
  },
  {
    id: 'bold',
    label: 'Bold',
    desc: 'Header con sfondo gradiente brand, titolo bianco — impattante e moderno.',
    preview: '◆',
  },
  {
    id: 'minimal',
    label: 'Minimal',
    desc: 'Layout editoriale, font serif, CTA come link — elegante e raffinato.',
    preview: '─',
  },
];

// ------------------------------------------------------------------ types

interface BrandingEditorProps {
  tenant: Pick<
    TenantRow,
    | 'id'
    | 'business_name'
    | 'brand_primary_color'
    | 'brand_logo_url'
    | 'email_from_name'
  > & {
    settings?: TenantSettings | null;
  };
}

interface RegenerateResult {
  style: EmailStyle;
  subject: string;
  headline: string;
  main_copy_1: string;
  main_copy_2: string;
  cta_text: string;
  rationale: string;
}

// ------------------------------------------------------------------ component

export function BrandingEditor({ tenant }: BrandingEditorProps) {
  const tenantSettings: TenantSettings = tenant.settings ?? {};

  const [color, setColor] = useState(
    tenant.brand_primary_color ?? '#0F766E',
  );
  const [logoUrl, setLogoUrl] = useState(tenant.brand_logo_url ?? '');
  const [fromName, setFromName] = useState(
    tenant.email_from_name ?? tenant.business_name ?? '',
  );
  const [style, setStyle] = useState<EmailStyle>(
    (tenantSettings.email_style as EmailStyle) ?? 'classic',
  );
  const [template, setTemplate] = useState<'b2b' | 'b2c'>('b2c');

  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  // AI regeneration
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiResult, setAiResult] = useState<RegenerateResult | null>(null);

  // iframe preview state
  const [previewSrc, setPreviewSrc] = useState('');
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refreshPreview = useCallback(
    (c: string, name: string, tpl: 'b2b' | 'b2c', s: EmailStyle) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(async () => {
        setPreviewLoading(true);
        setPreviewError(false);
        const auth = await getAuthHeader();
        const url = buildPreviewUrl(tpl, c, name, s);
        try {
          const res = await fetch(url, { headers: auth });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const html = await res.text();
          setPreviewSrc(html);
        } catch {
          setPreviewError(true);
        } finally {
          setPreviewLoading(false);
        }
      }, 600);
    },
    [],
  );

  useEffect(() => {
    refreshPreview(color, fromName, template, style);
  }, [color, fromName, template, style, refreshPreview]);

  async function handleAiRegenerate() {
    setAiLoading(true);
    setAiError(null);
    setAiResult(null);
    try {
      const auth = await getAuthHeader();
      const res = await fetch(`${API_URL}/v1/branding/regenerate-email`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...auth },
        body: JSON.stringify({ subject_type: template, save: true }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(
          (err as { detail?: string }).detail ?? `HTTP ${res.status}`,
        );
      }
      const data: RegenerateResult = await res.json();
      setAiResult(data);
      // Apply recommended style — preview will refresh automatically via useEffect
      setStyle(data.style);
    } catch (e) {
      setAiError((e as Error).message);
    } finally {
      setAiLoading(false);
    }
  }

  function handleSave() {
    setSaveError(null);
    startTransition(async () => {
      try {
        await patchTenant({
          brand_primary_color: color,
          brand_logo_url: logoUrl,
          email_from_name: fromName,
        });
        setSaved(true);
        setTimeout(() => setSaved(false), 3000);
      } catch (e) {
        setSaveError((e as Error).message);
      }
    });
  }

  const isDirty =
    color !== (tenant.brand_primary_color ?? '#0F766E') ||
    logoUrl !== (tenant.brand_logo_url ?? '') ||
    fromName !== (tenant.email_from_name ?? tenant.business_name ?? '');

  return (
    <div className="grid gap-8 lg:grid-cols-[1fr_460px]">
      {/* ── Left: controls ── */}
      <div className="space-y-7">

        {/* Color */}
        <div>
          <label className="block text-sm font-semibold text-on-surface">
            Colore principale
          </label>
          <p className="mt-0.5 text-xs text-on-surface-variant">
            Usato per la barra superiore, l&apos;header gradiente (Bold) e il CTA.
          </p>
          <div className="mt-3 flex items-center gap-3">
            <input
              type="color"
              value={color}
              onChange={(e) => setColor(e.target.value)}
              className="h-10 w-14 cursor-pointer rounded-md border border-outline-variant/40 bg-transparent p-0.5"
            />
            <input
              type="text"
              value={color}
              maxLength={7}
              onChange={(e) => {
                const v = e.target.value;
                if (/^#[0-9A-Fa-f]{0,6}$/.test(v)) setColor(v);
              }}
              className="w-28 rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-3 py-2 font-mono text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary/60"
            />
            <button
              type="button"
              onClick={() => setColor(tenant.brand_primary_color ?? '#0F766E')}
              className="text-xs text-on-surface-variant hover:text-on-surface"
            >
              Ripristina
            </button>
          </div>
        </div>

        {/* Logo URL */}
        <div>
          <label className="block text-sm font-semibold text-on-surface">
            URL logo (opzionale)
          </label>
          <p className="mt-0.5 text-xs text-on-surface-variant">
            PNG o SVG, min 200&nbsp;×&nbsp;50 px, sfondo trasparente. Appare
            sopra il contenuto in Classic e come header in Bold/Minimal.
          </p>
          <input
            type="url"
            value={logoUrl}
            placeholder="https://cdn.tuodominio.it/logo.png"
            onChange={(e) => setLogoUrl(e.target.value)}
            className="mt-2 w-full rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:ring-2 focus:ring-primary/60"
          />
          {logoUrl && (
            <div className="mt-2 flex h-12 items-center rounded-md border border-dashed border-outline-variant/40 bg-surface-container px-3">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={logoUrl}
                alt="anteprima logo"
                className="max-h-8 max-w-[160px] object-contain"
                onError={(e) => (e.currentTarget.style.display = 'none')}
              />
            </div>
          )}
        </div>

        {/* Email from name */}
        <div>
          <label className="block text-sm font-semibold text-on-surface">
            Nome mittente email
          </label>
          <p className="mt-0.5 text-xs text-on-surface-variant">
            Appare nell&apos;inbox come{' '}
            <span className="font-mono">
              Rossi Solar &lt;outreach@tuodominio.it&gt;
            </span>.
          </p>
          <input
            type="text"
            value={fromName}
            maxLength={100}
            onChange={(e) => setFromName(e.target.value)}
            className="mt-2 w-full rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:ring-2 focus:ring-primary/60"
          />
        </div>

        {/* Visual style picker */}
        <div>
          <label className="block text-sm font-semibold text-on-surface">
            Stile visivo email
          </label>
          <p className="mt-0.5 text-xs text-on-surface-variant">
            Scegli il layout grafico applicato a tutte le email outreach.
          </p>
          <div className="mt-3 grid grid-cols-3 gap-3">
            {EMAIL_STYLES.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => setStyle(s.id)}
                className={cn(
                  'rounded-xl border-2 p-3 text-left transition-all',
                  style === s.id
                    ? 'border-primary bg-primary-container/20'
                    : 'border-outline-variant/30 bg-surface-container-lowest hover:border-outline-variant/60',
                )}
              >
                <div
                  className={cn(
                    'mb-2 flex h-8 items-center justify-center rounded-md text-lg font-bold',
                    style === s.id
                      ? 'bg-primary/10 text-primary'
                      : 'bg-surface-container text-on-surface-variant',
                  )}
                >
                  {s.preview}
                </div>
                <p
                  className={cn(
                    'text-xs font-semibold',
                    style === s.id ? 'text-primary' : 'text-on-surface',
                  )}
                >
                  {s.label}
                </p>
                <p className="mt-0.5 text-[10px] text-on-surface-variant leading-snug">
                  {s.desc}
                </p>
              </button>
            ))}
          </div>
        </div>

        {/* AI regenerate */}
        <div className="rounded-xl border border-primary/20 bg-primary-container/10 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-primary">
                🎨 Rigenera contenuto con AI
              </p>
              <p className="mt-0.5 text-xs text-on-surface-variant">
                Claude scrive headline, testo e CTA basandosi sul tuo brand e
                sceglie lo stile visivo più adatto al segmento.
              </p>
            </div>
            <button
              type="button"
              disabled={aiLoading}
              onClick={handleAiRegenerate}
              className="shrink-0 rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-on-primary transition-opacity disabled:opacity-50 hover:opacity-90"
            >
              {aiLoading ? 'Generazione…' : '✨ Genera'}
            </button>
          </div>

          {/* Segment toggle */}
          <div className="mt-3 flex items-center gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Segmento:
            </span>
            <div className="flex overflow-hidden rounded-full border border-outline-variant/30 bg-surface-container-lowest text-xs">
              {(['b2c', 'b2b'] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setTemplate(t)}
                  className={cn(
                    'px-3 py-1 font-semibold transition-colors',
                    template === t
                      ? 'bg-primary text-on-primary'
                      : 'text-on-surface-variant hover:bg-surface-container',
                  )}
                >
                  {t.toUpperCase()}
                </button>
              ))}
            </div>
          </div>

          {aiError && (
            <p className="mt-2 text-xs text-error">{aiError}</p>
          )}

          {aiResult && (
            <div className="mt-3 space-y-2 rounded-lg border border-outline-variant/20 bg-surface-container-lowest p-3">
              <div className="flex items-center gap-2">
                <span className="inline-flex items-center rounded-full bg-primary-container/50 px-2 py-0.5 text-[10px] font-bold text-on-primary-container uppercase">
                  {aiResult.style}
                </span>
                <span className="text-xs text-on-surface-variant italic">
                  {aiResult.rationale}
                </span>
              </div>
              <p className="text-xs text-on-surface">
                <span className="font-semibold">Oggetto:</span>{' '}
                {aiResult.subject}
              </p>
              <p className="text-xs text-on-surface">
                <span className="font-semibold">Headline:</span>{' '}
                {aiResult.headline}
              </p>
              <p className="text-xs text-on-surface">
                <span className="font-semibold">CTA:</span> {aiResult.cta_text}
              </p>
              <p className="text-[10px] text-on-surface-variant">
                ✓ Stile e testo salvati — l&apos;anteprima a destra si
                aggiorna automaticamente.
              </p>
            </div>
          )}
        </div>

        {/* Save */}
        <div className="flex items-center gap-3 pt-1">
          <button
            type="button"
            disabled={!isDirty || isPending}
            onClick={handleSave}
            className="rounded-lg bg-primary px-5 py-2 text-sm font-semibold text-on-primary transition-opacity disabled:opacity-40 hover:opacity-90"
          >
            {isPending ? 'Salvataggio…' : 'Salva modifiche'}
          </button>
          {saved && (
            <span className="text-sm font-medium text-primary">✓ Salvato</span>
          )}
          {saveError && (
            <span className="text-sm text-error">{saveError}</span>
          )}
        </div>
      </div>

      {/* ── Right: live preview ── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Anteprima email live
          </p>
          <div className="flex rounded-full border border-outline-variant/40 bg-surface-container text-xs">
            {(['b2c', 'b2b'] as const).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTemplate(t)}
                className={
                  template === t
                    ? 'rounded-full bg-primary px-3 py-1 font-semibold text-on-primary'
                    : 'px-3 py-1 text-on-surface-variant'
                }
              >
                {t.toUpperCase()}
              </button>
            ))}
          </div>
        </div>

        <div className="relative overflow-hidden rounded-xl border border-outline-variant/30 bg-surface-container-lowest shadow-sm">
          {/* Fake browser chrome */}
          <div className="flex items-center gap-1.5 border-b border-outline-variant/20 bg-surface-container px-3 py-2">
            <span className="h-2.5 w-2.5 rounded-full bg-error/60" />
            <span className="h-2.5 w-2.5 rounded-full bg-tertiary/60" />
            <span className="h-2.5 w-2.5 rounded-full bg-primary/60" />
            <span className="ml-3 flex-1 rounded bg-surface-container-high px-2 py-0.5 text-[10px] text-on-surface-variant">
              {style} · {template} · step 1
            </span>
          </div>

          {previewLoading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-surface-container/80">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
          )}

          {previewError ? (
            <div className="flex h-80 items-center justify-center text-sm text-on-surface-variant">
              Anteprima non disponibile
            </div>
          ) : (
            <iframe
              title="Email preview"
              srcDoc={previewSrc || undefined}
              sandbox="allow-same-origin"
              className="h-[520px] w-full"
              onLoad={() => setPreviewLoading(false)}
            />
          )}
        </div>

        <p className="text-[11px] text-on-surface-variant">
          L&apos;anteprima usa dati di esempio. I rendering 3D reali vengono
          inseriti dall&apos;agente di outreach al momento dell&apos;invio.
        </p>
      </div>
    </div>
  );
}
