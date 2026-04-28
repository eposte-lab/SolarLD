'use client';

/**
 * TemplateUploader — Sprint 9 Fase C.5.
 *
 * Provides a textarea editor + file drop-zone for uploading a
 * custom Jinja2 HTML email template. Shows:
 *   - Live required-variable checker (✓/✗ per GDPR var)
 *   - Upload / deactivate buttons
 *   - Upload status feedback
 */

import { useCallback, useRef, useState } from 'react';
import {
  uploadEmailTemplate,
  deactivateEmailTemplate,
  type TemplateInfo,
} from '@/lib/data/cluster-ab';

const REQUIRED_VARS = [
  'unsubscribe_url',
  'tracking_pixel_url',
  'tenant_legal_name',
  'tenant_vat_number',
  'tenant_legal_address',
];

interface TemplateUploaderProps {
  templateInfo: TemplateInfo | null;
  onSaved: () => void;
}

function extractJinjaVars(html: string): Set<string> {
  const regex = /\{\{\s*([\w.]+)/g;
  const found = new Set<string>();
  let match;
  while ((match = regex.exec(html)) !== null) {
    found.add((match[1] ?? '').split('.')[0] ?? match[1] ?? ''); // top-level var only
  }
  return found;
}

export function TemplateUploader({ templateInfo, onSaved }: TemplateUploaderProps) {
  const [html, setHtml] = useState('');
  const [status, setStatus] = useState<'idle' | 'uploading' | 'success' | 'error'>('idle');
  const [errorMsg, setErrorMsg] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const presentVars = extractJinjaVars(html);

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const content = ev.target?.result as string;
      setHtml(content ?? '');
    };
    reader.readAsText(file, 'UTF-8');
  }, []);

  const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => setHtml((ev.target?.result as string) ?? '');
    reader.readAsText(file, 'UTF-8');
  }, []);

  const handleUpload = useCallback(async () => {
    if (!html.trim()) return;
    setStatus('uploading');
    setErrorMsg('');
    try {
      await uploadEmailTemplate(html);
      setStatus('success');
      onSaved();
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ??
        (err instanceof Error ? err.message : 'Errore sconosciuto');
      setErrorMsg(detail);
      setStatus('error');
    }
  }, [html, onSaved]);

  const handleDeactivate = useCallback(async () => {
    await deactivateEmailTemplate();
    onSaved();
  }, [onSaved]);

  const allVarsPresent = REQUIRED_VARS.every((v) => presentVars.has(v));

  return (
    <div className="space-y-4">
      {/* Drop zone / paste area */}
      <div
        onDrop={handleDrop}
        onDragOver={(e) => e.preventDefault()}
        className="rounded-xl border-2 border-dashed border-outline/40 p-4 hover:border-primary/60 transition-colors"
      >
        <p className="text-sm text-on-surface-variant mb-2">
          Trascina un file <code>.html</code> qui o incolla il codice sotto:
        </p>
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="text-sm text-primary underline"
        >
          Seleziona file
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".html,.htm,.j2"
          className="hidden"
          onChange={handleFileChange}
        />
      </div>

      {/* Textarea editor */}
      <textarea
        value={html}
        onChange={(e) => setHtml(e.target.value)}
        placeholder='Incolla qui il tuo HTML Jinja2, es. <html>…{{ unsubscribe_url }}…</html>'
        rows={10}
        className="w-full rounded-xl border bg-surface px-3 py-2 font-mono text-xs leading-relaxed
                   focus:outline-none focus:ring-2 focus:ring-primary/40 resize-y"
        spellCheck={false}
      />

      {/* Required variable checker */}
      <div className="rounded-xl border bg-surface-variant/20 p-3 space-y-1">
        <p className="text-xs font-medium text-on-surface-variant mb-1">
          Variabili GDPR obbligatorie:
        </p>
        {REQUIRED_VARS.map((v) => (
          <div key={v} className="flex items-center gap-2 text-xs">
            <span
              className={
                presentVars.has(v)
                  ? 'text-green-600 font-bold'
                  : html.length > 0
                  ? 'text-red-500 font-bold'
                  : 'text-on-surface-variant'
              }
            >
              {presentVars.has(v) ? '✓' : html.length > 0 ? '✗' : '○'}
            </span>
            <code className="font-mono">{`{{ ${v} }}`}</code>
          </div>
        ))}
      </div>

      {/* Error message */}
      {status === 'error' && errorMsg && (
        <p className="text-sm text-red-600 rounded-lg bg-red-50 px-3 py-2">
          {errorMsg}
        </p>
      )}
      {status === 'success' && (
        <p className="text-sm text-green-700 rounded-lg bg-green-50 px-3 py-2">
          Template salvato e attivato con successo.
        </p>
      )}

      {/* Actions */}
      <div className="flex gap-3">
        <button
          type="button"
          onClick={handleUpload}
          disabled={!html.trim() || !allVarsPresent || status === 'uploading'}
          className="flex-1 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white
                     disabled:opacity-50 disabled:cursor-not-allowed hover:bg-primary/90 transition-colors"
        >
          {status === 'uploading' ? 'Salvataggio…' : 'Salva e attiva'}
        </button>

        {templateInfo?.active && (
          <button
            type="button"
            onClick={handleDeactivate}
            className="rounded-xl border border-outline/40 px-4 py-2 text-sm font-medium
                       hover:bg-surface-variant/30 transition-colors"
          >
            Disattiva
          </button>
        )}
      </div>
    </div>
  );
}
