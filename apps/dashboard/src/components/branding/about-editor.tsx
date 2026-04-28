'use client';

/**
 * AboutEditor — tenant "Chi siamo" editor.
 *
 * Sprint 8 Fase A.2: powers /settings/branding/about. Persists via
 * `PATCH /v1/branding/about` (see `apps/api/src/routes/branding.py`).
 *
 * Surfaced in the public lead portal `<AboutSection>` (Fase A.3). The
 * editor is intentionally opinionated:
 *   - markdown free-text capped at 4 KB (matches DB CHECK)
 *   - tagline 120 chars (single line under business name)
 *   - certifications: chip multi-input (max 12)
 *   - hero image: simple URL field (uses Supabase Storage signed URLs
 *     today; an upload flow piggy-backs on LogoUpload in a future iter)
 *
 * The save flow is optimistic-friendly (button state shows "Salvato"
 * for 2 s after a successful PATCH) but does NOT auto-save: the user
 * must click "Salva" to commit. This avoids partial saves while typing
 * markdown.
 */

import { Loader2, Plus, X } from 'lucide-react';
import { useState } from 'react';

import { apiFetch } from '@/lib/api-client';

const MD_MAX_BYTES = 4096;
const TAGLINE_MAX = 120;
const CERT_MAX = 12;

const TEAM_SIZE_OPTIONS = [
  { value: '', label: '—' },
  { value: '1-2', label: '1–2 persone' },
  { value: '3-5', label: '3–5 persone' },
  { value: '6-10', label: '6–10 persone' },
  { value: '11-25', label: '11–25 persone' },
  { value: '26-50', label: '26–50 persone' },
  { value: '50+', label: 'Oltre 50' },
];

export interface AboutEditorValues {
  about_md: string | null;
  about_year_founded: number | null;
  about_team_size: string | null;
  about_certifications: string[];
  about_hero_image_url: string | null;
  about_tagline: string | null;
}

function utf8ByteLength(s: string): number {
  // TextEncoder counts bytes the way Postgres octet_length does.
  return new TextEncoder().encode(s).length;
}

export function AboutEditor({ initial }: { initial: AboutEditorValues }) {
  const [md, setMd] = useState(initial.about_md ?? '');
  const [tagline, setTagline] = useState(initial.about_tagline ?? '');
  const [year, setYear] = useState<string>(
    initial.about_year_founded != null ? String(initial.about_year_founded) : '',
  );
  const [teamSize, setTeamSize] = useState<string>(initial.about_team_size ?? '');
  const [certs, setCerts] = useState<string[]>(initial.about_certifications ?? []);
  const [certDraft, setCertDraft] = useState('');
  const [heroUrl, setHeroUrl] = useState(initial.about_hero_image_url ?? '');

  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mdBytes = utf8ByteLength(md);
  const mdOver = mdBytes > MD_MAX_BYTES;
  const taglineOver = tagline.length > TAGLINE_MAX;

  function addCert() {
    const v = certDraft.trim();
    if (!v) return;
    if (certs.some((c) => c.toLowerCase() === v.toLowerCase())) {
      setCertDraft('');
      return;
    }
    if (certs.length >= CERT_MAX) return;
    setCerts((prev) => [...prev, v.slice(0, 80)]);
    setCertDraft('');
  }

  function removeCert(idx: number) {
    setCerts((prev) => prev.filter((_, i) => i !== idx));
  }

  async function handleSave() {
    if (mdOver || taglineOver) return;
    setSaving(true);
    setError(null);
    try {
      const yearNum = year.trim() ? Number(year) : null;
      await apiFetch('/v1/branding/about', {
        method: 'PATCH',
        body: JSON.stringify({
          about_md: md.trim() || null,
          about_year_founded:
            yearNum != null && Number.isFinite(yearNum) && yearNum >= 1900 && yearNum <= 2100
              ? yearNum
              : null,
          about_team_size: teamSize.trim() || null,
          about_certifications: certs,
          about_hero_image_url: heroUrl.trim() || null,
          about_tagline: tagline.trim() || null,
        }),
      });
      setSavedAt(Date.now());
      setTimeout(() => {
        setSavedAt((stamp) => (stamp && Date.now() - stamp >= 2000 ? null : stamp));
      }, 2100);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Errore salvataggio');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* Tagline */}
      <Field
        label="Tagline"
        hint={`Una riga sotto il nome dell'azienda nel portale (${tagline.length}/${TAGLINE_MAX})`}
        error={taglineOver ? `Massimo ${TAGLINE_MAX} caratteri` : null}
      >
        <input
          type="text"
          value={tagline}
          onChange={(e) => setTagline(e.target.value.slice(0, TAGLINE_MAX + 20))}
          placeholder="Installatore qualificato dal 2015 in Campania"
          className="w-full rounded-xl bg-surface-container-high px-4 py-2.5 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary"
        />
      </Field>

      {/* Markdown narrative */}
      <Field
        label="Narrativa (Markdown)"
        hint={`Storia, missione, valori. ${mdBytes} / ${MD_MAX_BYTES} byte`}
        error={mdOver ? `Massimo ${MD_MAX_BYTES} byte (~4000 caratteri)` : null}
      >
        <textarea
          value={md}
          onChange={(e) => setMd(e.target.value)}
          placeholder={
            "## La nostra storia\n\nDa oltre 10 anni installiamo impianti fotovoltaici…"
          }
          rows={12}
          className="w-full resize-y rounded-xl bg-surface-container-high px-4 py-3 font-mono text-[13px] leading-6 text-on-surface outline-none focus:ring-2 focus:ring-primary"
        />
      </Field>

      <div className="grid gap-6 md:grid-cols-2">
        <Field label="Anno di fondazione" hint="Es. 2015">
          <input
            type="number"
            value={year}
            onChange={(e) => setYear(e.target.value)}
            min={1900}
            max={2100}
            placeholder="2015"
            className="w-full rounded-xl bg-surface-container-high px-4 py-2.5 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary"
          />
        </Field>

        <Field label="Dimensione del team">
          <select
            value={teamSize}
            onChange={(e) => setTeamSize(e.target.value)}
            className="w-full rounded-xl bg-surface-container-high px-4 py-2.5 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary"
          >
            {TEAM_SIZE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </Field>
      </div>

      {/* Certifications */}
      <Field
        label="Certificazioni"
        hint={`Chip mostrati nella sezione Chi siamo (${certs.length}/${CERT_MAX})`}
      >
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            {certs.map((c, i) => (
              <span
                key={`${c}-${i}`}
                className="inline-flex items-center gap-1.5 rounded-full bg-primary/15 px-3 py-1 text-xs font-semibold text-primary"
              >
                {c}
                <button
                  type="button"
                  onClick={() => removeCert(i)}
                  className="opacity-60 transition-opacity hover:opacity-100"
                  aria-label={`Rimuovi ${c}`}
                >
                  <X size={12} strokeWidth={2.25} />
                </button>
              </span>
            ))}
          </div>
          {certs.length < CERT_MAX && (
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={certDraft}
                onChange={(e) => setCertDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    addCert();
                  }
                }}
                placeholder="UNI 11352, GSE Albo, …"
                className="flex-1 rounded-xl bg-surface-container-high px-4 py-2 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary"
              />
              <button
                type="button"
                onClick={addCert}
                disabled={!certDraft.trim()}
                className="inline-flex items-center gap-1.5 rounded-xl bg-surface-container-high px-3 py-2 text-sm font-semibold text-on-surface transition-opacity hover:opacity-80 disabled:opacity-40"
              >
                <Plus size={14} strokeWidth={2.25} /> Aggiungi
              </button>
            </div>
          )}
        </div>
      </Field>

      {/* Hero image URL */}
      <Field
        label="Immagine hero (URL)"
        hint="Foto del team o cantiere. PNG/JPG, ~1600×900 px consigliato."
      >
        <input
          type="url"
          value={heroUrl}
          onChange={(e) => setHeroUrl(e.target.value)}
          placeholder="https://…"
          className="w-full rounded-xl bg-surface-container-high px-4 py-2.5 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary"
        />
      </Field>

      {/* Save bar */}
      <div className="flex items-center justify-between gap-3 border-t border-outline-variant/40 pt-5">
        <p className="text-xs text-on-surface-variant">
          Le modifiche compaiono sul portale lead pubblico una volta salvate.
        </p>
        <div className="flex items-center gap-3">
          {error && <span className="text-xs font-semibold text-error">{error}</span>}
          {savedAt && !error && (
            <span className="text-xs font-semibold text-primary">Salvato</span>
          )}
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || mdOver || taglineOver}
            className="inline-flex items-center gap-2 rounded-full bg-primary px-5 py-2 text-sm font-semibold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-95 disabled:opacity-40"
          >
            {saving ? (
              <>
                <Loader2 size={14} className="animate-spin" /> Salvo…
              </>
            ) : (
              'Salva'
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string | null;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-1.5 flex items-baseline justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          {label}
        </span>
        {hint && (
          <span
            className={`text-[11px] ${
              error ? 'text-error' : 'text-on-surface-muted'
            }`}
          >
            {error ?? hint}
          </span>
        )}
      </label>
      {children}
    </div>
  );
}
