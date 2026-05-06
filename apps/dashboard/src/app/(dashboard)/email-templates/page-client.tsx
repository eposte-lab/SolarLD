'use client';

/**
 * EmailTemplatesClient — Template email per campagne custom.
 *
 * States:
 *   'list'   — Index of all templates with create + edit + delete actions.
 *   'editor' — Full-width editor: name/subject inputs + variable picker +
 *              HTML textarea + sandboxed live preview iframe.
 *
 * Variables available as {{ var_name }} Jinja2 placeholders.
 * GDPR-required variables (unsubscribe_url, tenant_legal_name,
 * tenant_vat_number, tenant_legal_address) must be present to save.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  type EmailTemplate,
  type EmailTemplateRow,
  type TemplateVariable,
  createEmailTemplate,
  deleteEmailTemplate,
  getEmailTemplate,
  listEmailTemplates,
  listTemplateVariables,
  previewEmailTemplate,
  updateEmailTemplate,
  validateEmailTemplate,
} from '@/lib/data/email-templates';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type View = 'list' | 'editor';

interface EditorState {
  id: string | null; // null = new
  name: string;
  subject: string;
  html: string;
  plain_text: string;
}

const EMPTY_EDITOR: EditorState = {
  id: null,
  name: '',
  subject: '',
  html: '',
  plain_text: '',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString('it-IT', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}

const REQUIRED_VARS = new Set([
  'unsubscribe_url',
  'tenant_legal_name',
  'tenant_vat_number',
  'tenant_legal_address',
]);

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function EmailTemplatesClient() {
  const [view, setView] = useState<View>('list');
  const [templates, setTemplates] = useState<EmailTemplateRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [variables, setVariables] = useState<TemplateVariable[]>([]);

  // Editor state
  const [editor, setEditor] = useState<EditorState>(EMPTY_EDITOR);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null); // template id

  // Preview
  const [previewHtml, setPreviewHtml] = useState<string>('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const previewDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load list + variables on mount
  useEffect(() => {
    Promise.all([loadList(), loadVariables()]);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function loadList() {
    setLoading(true);
    try {
      const res = await listEmailTemplates();
      setTemplates(res.items);
    } finally {
      setLoading(false);
    }
  }

  async function loadVariables() {
    try {
      const res = await listTemplateVariables();
      setVariables(res.variables);
    } catch {
      // non-critical
    }
  }

  // ── Live preview debounce ──────────────────────────────────────────
  useEffect(() => {
    if (view !== 'editor') return;
    if (!editor.html) {
      setPreviewHtml('');
      return;
    }
    if (previewDebounceRef.current) clearTimeout(previewDebounceRef.current);
    previewDebounceRef.current = setTimeout(() => {
      renderPreview();
    }, 800);
    return () => {
      if (previewDebounceRef.current) clearTimeout(previewDebounceRef.current);
    };
  }, [editor.html, view]); // eslint-disable-line react-hooks/exhaustive-deps

  async function renderPreview() {
    if (!editor.id) {
      // For unsaved templates, render locally via simple variable substitution
      // (no server round-trip needed for the inline preview).
      setPreviewHtml(localPreview(editor.html));
      return;
    }
    setPreviewLoading(true);
    try {
      const res = await previewEmailTemplate(editor.id);
      setPreviewHtml(res.html);
    } catch {
      setPreviewHtml(localPreview(editor.html));
    } finally {
      setPreviewLoading(false);
    }
  }

  function localPreview(html: string): string {
    const samples: Record<string, string> = {
      greeting_name: 'Mario Rossi',
      business_name: 'Studio Rossi Amministrazioni',
      hq_address: 'Via Roma 42',
      hq_cap: '80100',
      hq_city: 'Napoli',
      hq_province: 'NA',
      phone: '+39 081 123 4567',
      recipient_email: 'mario@esempio.it',
      sender_first_name: 'Alfonso',
      tenant_name: 'SolarTech',
      brand_logo_url: '',
      unsubscribe_url: 'https://solarld.app/optout/preview',
      tenant_legal_name: 'SolarTech S.r.l.',
      tenant_vat_number: 'IT12345678901',
      tenant_legal_address: 'Via Milano 10, 20100 Milano MI',
      tracking_pixel_url: 'https://solarld.app/track/preview',
    };
    return html.replace(/\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g, (_, key) => {
      return samples[key] ?? `[${key}]`;
    });
  }

  // ── Open editor for new template ─────────────────────────────────
  function openNewEditor() {
    setEditor(EMPTY_EDITOR);
    setSaveError(null);
    setValidationErrors([]);
    setPreviewHtml('');
    setView('editor');
  }

  // ── Open editor for existing template ───────────────────────────
  async function openEditEditor(id: string) {
    setSaveError(null);
    setValidationErrors([]);
    setView('editor');
    setLoading(true);
    try {
      const tpl = await getEmailTemplate(id);
      setEditor({
        id: tpl.id,
        name: tpl.name,
        subject: tpl.subject,
        html: tpl.html,
        plain_text: tpl.plain_text ?? '',
      });
      setPreviewHtml(localPreview(tpl.html));
    } finally {
      setLoading(false);
    }
  }

  // ── Save (create or update) ───────────────────────────────────────
  async function handleSave() {
    setSaveError(null);
    setValidationErrors([]);

    // Client-side GDPR check before hitting the server.
    const missingRequired = [...REQUIRED_VARS].filter(
      (v) => !editor.html.includes(`{{ ${v} }}`) && !editor.html.includes(`{{${v}}}`),
    );
    if (missingRequired.length > 0) {
      setValidationErrors(missingRequired);
      return;
    }

    setSaving(true);
    try {
      if (editor.id) {
        await updateEmailTemplate(editor.id, {
          name: editor.name,
          subject: editor.subject,
          html: editor.html,
          plain_text: editor.plain_text || undefined,
        });
      } else {
        const created = await createEmailTemplate({
          name: editor.name,
          subject: editor.subject,
          html: editor.html,
          plain_text: editor.plain_text || undefined,
        });
        setEditor((e) => ({ ...e, id: created.id }));
      }
      await loadList();
      setView('list');
    } catch (err: unknown) {
      const msg = extractErrorMessage(err);
      setSaveError(msg);
    } finally {
      setSaving(false);
    }
  }

  // ── Delete ─────────────────────────────────────────────────────────
  async function handleDelete(id: string) {
    try {
      await deleteEmailTemplate(id);
      setDeleteConfirm(null);
      await loadList();
    } catch {
      // show inline error TODO
    }
  }

  // ── Variable picker insert ─────────────────────────────────────────
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  function insertVariable(slug: string) {
    const tag = `{{ ${slug} }}`;
    const ta = textareaRef.current;
    if (!ta) {
      setEditor((e) => ({ ...e, html: e.html + tag }));
      return;
    }
    const start = ta.selectionStart ?? ta.value.length;
    const end = ta.selectionEnd ?? ta.value.length;
    const newVal = ta.value.slice(0, start) + tag + ta.value.slice(end);
    setEditor((e) => ({ ...e, html: newVal }));
    // Restore cursor after the inserted tag.
    requestAnimationFrame(() => {
      ta.setSelectionRange(start + tag.length, start + tag.length);
      ta.focus();
    });
  }

  // ── Render ──────────────────────────────────────────────────────────

  if (view === 'editor') {
    return (
      <EditorView
        editor={editor}
        setEditor={setEditor}
        variables={variables}
        previewHtml={previewHtml}
        previewLoading={previewLoading}
        saving={saving}
        saveError={saveError}
        validationErrors={validationErrors}
        textareaRef={textareaRef}
        onInsertVariable={insertVariable}
        onSave={handleSave}
        onCancel={() => setView('list')}
      />
    );
  }

  return (
    <ListView
      templates={templates}
      loading={loading}
      deleteConfirm={deleteConfirm}
      setDeleteConfirm={setDeleteConfirm}
      onNew={openNewEditor}
      onEdit={openEditEditor}
      onDelete={handleDelete}
    />
  );
}

// ---------------------------------------------------------------------------
// ListView
// ---------------------------------------------------------------------------

function ListView({
  templates,
  loading,
  deleteConfirm,
  setDeleteConfirm,
  onNew,
  onEdit,
  onDelete,
}: {
  templates: EmailTemplateRow[];
  loading: boolean;
  deleteConfirm: string | null;
  setDeleteConfirm: (id: string | null) => void;
  onNew: () => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-on-surface">Template email campagne</h1>
          <p className="mt-1 text-sm text-on-surface-variant">
            Crea template HTML con variabili personalizzate per le campagne{' '}
            <em>generic_outreach</em> di Trova aziende.
          </p>
        </div>
        <button
          onClick={onNew}
          className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-700 active:bg-teal-800"
        >
          + Nuovo template
        </button>
      </div>

      {/* Table */}
      {loading ? (
        <div className="py-16 text-center text-sm text-on-surface-variant">
          Caricamento…
        </div>
      ) : templates.length === 0 ? (
        <div className="rounded-xl border border-dashed border-on-surface/20 py-16 text-center">
          <p className="text-sm text-on-surface-variant">Nessun template ancora.</p>
          <button
            onClick={onNew}
            className="mt-3 text-sm font-semibold text-teal-600 hover:underline"
          >
            Crea il primo template →
          </button>
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-on-surface/10 bg-surface">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-on-surface/10 text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                <th className="px-4 py-3 text-left">Nome</th>
                <th className="px-4 py-3 text-left">Oggetto</th>
                <th className="px-4 py-3 text-left">Variabili</th>
                <th className="px-4 py-3 text-left">Modificato</th>
                <th className="px-4 py-3 text-right">Azioni</th>
              </tr>
            </thead>
            <tbody>
              {templates.map((tpl) => (
                <tr
                  key={tpl.id}
                  className="border-b border-on-surface/5 last:border-0 hover:bg-on-surface/[0.02]"
                >
                  <td className="px-4 py-3 font-medium text-on-surface">{tpl.name}</td>
                  <td className="px-4 py-3 text-on-surface-variant">{tpl.subject}</td>
                  <td className="px-4 py-3">
                    <span className="rounded-full bg-teal-50 px-2 py-0.5 text-xs text-teal-700">
                      {(tpl.variables_used as string[]).length} var.
                    </span>
                  </td>
                  <td className="px-4 py-3 text-on-surface-variant">{fmtDate(tpl.updated_at)}</td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        onClick={() => onEdit(tpl.id)}
                        className="rounded px-2 py-1 text-xs font-semibold text-teal-600 hover:bg-teal-50"
                      >
                        Modifica
                      </button>
                      {deleteConfirm === tpl.id ? (
                        <>
                          <button
                            onClick={() => onDelete(tpl.id)}
                            className="rounded px-2 py-1 text-xs font-semibold text-red-600 hover:bg-red-50"
                          >
                            Conferma
                          </button>
                          <button
                            onClick={() => setDeleteConfirm(null)}
                            className="rounded px-2 py-1 text-xs text-on-surface-variant hover:bg-on-surface/5"
                          >
                            Annulla
                          </button>
                        </>
                      ) : (
                        <button
                          onClick={() => setDeleteConfirm(tpl.id)}
                          className="rounded px-2 py-1 text-xs text-on-surface-variant hover:text-red-600"
                        >
                          Elimina
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Help */}
      <div className="rounded-xl bg-teal-50 p-4 text-sm text-teal-800">
        <p className="font-semibold">Come funzionano i template</p>
        <p className="mt-1 text-teal-700">
          Scrivi HTML normale con segnaposto Jinja2 come{' '}
          <code className="rounded bg-teal-100 px-1 py-0.5 font-mono text-xs">
            {'{{ business_name }}'}
          </code>
          . Il sistema sostituisce le variabili con i dati reali di ogni azienda al momento
          dell&apos;invio. I template vanno poi associati a una lista in{' '}
          <strong>Trova aziende</strong>.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// EditorView
// ---------------------------------------------------------------------------

function EditorView({
  editor,
  setEditor,
  variables,
  previewHtml,
  previewLoading,
  saving,
  saveError,
  validationErrors,
  textareaRef,
  onInsertVariable,
  onSave,
  onCancel,
}: {
  editor: EditorState;
  setEditor: React.Dispatch<React.SetStateAction<EditorState>>;
  variables: TemplateVariable[];
  previewHtml: string;
  previewLoading: boolean;
  saving: boolean;
  saveError: string | null;
  validationErrors: string[];
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  onInsertVariable: (slug: string) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const isNew = !editor.id;

  return (
    <div className="flex h-[calc(100vh-80px)] flex-col gap-0">
      {/* Top bar */}
      <div className="flex shrink-0 items-center justify-between border-b border-on-surface/10 bg-surface px-4 py-3">
        <div className="flex items-center gap-3">
          <button
            onClick={onCancel}
            className="text-sm text-on-surface-variant hover:text-on-surface"
          >
            ← Template email
          </button>
          <span className="text-on-surface/20">/</span>
          <span className="text-sm font-semibold text-on-surface">
            {isNew ? 'Nuovo template' : editor.name}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onCancel}
            className="rounded-lg px-3 py-1.5 text-sm text-on-surface-variant hover:bg-on-surface/5"
          >
            Annulla
          </button>
          <button
            onClick={onSave}
            disabled={saving || !editor.name || !editor.subject || !editor.html}
            className="rounded-lg bg-teal-600 px-4 py-1.5 text-sm font-semibold text-white
              hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? 'Salvataggio…' : 'Salva'}
          </button>
        </div>
      </div>

      {/* Error banners */}
      {validationErrors.length > 0 && (
        <div className="shrink-0 border-b border-red-200 bg-red-50 px-4 py-2">
          <p className="text-sm font-semibold text-red-700">
            Variabili GDPR obbligatorie mancanti:
          </p>
          <p className="text-xs text-red-600">
            {validationErrors.map((v) => `{{ ${v} }}`).join(', ')}
          </p>
        </div>
      )}
      {saveError && (
        <div className="shrink-0 border-b border-red-200 bg-red-50 px-4 py-2">
          <p className="text-sm text-red-700">{saveError}</p>
        </div>
      )}

      {/* Main two-column layout */}
      <div className="flex flex-1 gap-0 overflow-hidden">
        {/* Left — editor */}
        <div className="flex w-1/2 flex-col gap-4 overflow-y-auto border-r border-on-surface/10 p-4">
          {/* Name + Subject */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-semibold text-on-surface-variant">
                Nome template
              </label>
              <input
                type="text"
                value={editor.name}
                onChange={(e) => setEditor((s) => ({ ...s, name: e.target.value }))}
                placeholder="Es. Campagna amm. condominio"
                className="w-full rounded-lg border border-on-surface/20 bg-surface px-3 py-2 text-sm
                  text-on-surface placeholder:text-on-surface/40 focus:border-teal-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold text-on-surface-variant">
                Oggetto email
              </label>
              <input
                type="text"
                value={editor.subject}
                onChange={(e) => setEditor((s) => ({ ...s, subject: e.target.value }))}
                placeholder="Es. Una proposta per {{ business_name }}"
                className="w-full rounded-lg border border-on-surface/20 bg-surface px-3 py-2 text-sm
                  text-on-surface placeholder:text-on-surface/40 focus:border-teal-500 focus:outline-none"
              />
            </div>
          </div>

          {/* Variable picker */}
          <div>
            <p className="mb-2 text-xs font-semibold text-on-surface-variant">
              Inserisci variabile (click per inserire nel cursore)
            </p>
            <div className="flex flex-wrap gap-1.5">
              {variables.map((v) => (
                <button
                  key={v.slug}
                  title={`Esempio: ${v.example}`}
                  onClick={() => onInsertVariable(v.slug)}
                  className={`rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors
                    ${REQUIRED_VARS.has(v.slug)
                      ? 'bg-amber-100 text-amber-800 hover:bg-amber-200'
                      : 'bg-teal-50 text-teal-700 hover:bg-teal-100'
                    }`}
                >
                  {v.label}
                </button>
              ))}
            </div>
            <p className="mt-1 text-[11px] text-on-surface/50">
              ✱ = obbligatorio per GDPR
            </p>
          </div>

          {/* HTML editor */}
          <div className="flex flex-1 flex-col">
            <label className="mb-1 block text-xs font-semibold text-on-surface-variant">
              HTML template
            </label>
            <textarea
              ref={textareaRef as React.RefObject<HTMLTextAreaElement>}
              value={editor.html}
              onChange={(e) => setEditor((s) => ({ ...s, html: e.target.value }))}
              spellCheck={false}
              className="flex-1 resize-none rounded-lg border border-on-surface/20 bg-gray-950 p-3
                font-mono text-xs leading-relaxed text-green-300
                focus:border-teal-500 focus:outline-none"
              style={{ minHeight: '360px' }}
              placeholder={`<!DOCTYPE html>\n<html>\n<body>\n  <p>Gentile {{ greeting_name }},</p>\n  <p>…</p>\n  <a href="{{ unsubscribe_url }}">Disiscriviti</a>\n  <p>{{ tenant_legal_name }} — P.IVA {{ tenant_vat_number }}</p>\n  <p>{{ tenant_legal_address }}</p>\n</body>\n</html>`}
            />
          </div>

          {/* Plain text (optional, collapsible) */}
          <details>
            <summary className="cursor-pointer text-xs font-semibold text-on-surface-variant">
              Testo plain (opzionale — generato automaticamente se omesso)
            </summary>
            <textarea
              value={editor.plain_text}
              onChange={(e) => setEditor((s) => ({ ...s, plain_text: e.target.value }))}
              className="mt-2 w-full resize-none rounded-lg border border-on-surface/20 bg-surface p-3
                font-mono text-xs text-on-surface focus:border-teal-500 focus:outline-none"
              rows={6}
              placeholder="Versione testuale dell'email (senza HTML)."
            />
          </details>
        </div>

        {/* Right — preview */}
        <div className="flex w-1/2 flex-col overflow-hidden">
          <div className="flex shrink-0 items-center justify-between border-b border-on-surface/10 px-4 py-2">
            <span className="text-xs font-semibold text-on-surface-variant">
              Anteprima live
            </span>
            {previewLoading && (
              <span className="text-xs text-on-surface-variant">Aggiornamento…</span>
            )}
          </div>
          {previewHtml ? (
            <iframe
              key={previewHtml.slice(0, 40)}
              srcDoc={previewHtml}
              sandbox="allow-same-origin"
              title="Anteprima template email"
              className="flex-1 bg-white"
            />
          ) : (
            <div className="flex flex-1 items-center justify-center bg-gray-50 text-sm text-on-surface-variant">
              L&apos;anteprima appare qui mentre scrivi l&apos;HTML →
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

function extractErrorMessage(err: unknown): string {
  if (!err) return 'Errore sconosciuto';
  if (typeof err === 'string') return err;
  if (typeof err === 'object') {
    const e = err as Record<string, unknown>;
    // FastAPI 422 detail
    const detail = e['detail'];
    if (detail && typeof detail === 'object') {
      const d = detail as Record<string, unknown>;
      if (d['message']) return String(d['message']);
    }
    if (typeof detail === 'string') return detail;
    if (e['message']) return String(e['message']);
  }
  return 'Errore durante il salvataggio.';
}
