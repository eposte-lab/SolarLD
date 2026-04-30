'use client';

/**
 * PracticeUploadsPanel — Sprint 4 OCR pipeline UI.
 *
 * Lets the installer drag-drop customer-supplied documents (visura
 * camerale, carta d'identità, visura catastale, recent bolletta) onto
 * the practice detail page.  Claude Vision extracts structured fields
 * server-side; this panel shows the per-upload status and exposes a
 * one-click "Applica suggerimenti" button that writes the extracted
 * values to tenant / subject / practice via the apply endpoint.
 *
 * Polling: any row with extraction_status='pending' triggers a 3s
 * refetch loop until all rows resolve to success/manual_required/failed.
 * Mirrors the existing pattern on practice document generation.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  Check,
  Download,
  FileText,
  Loader2,
  Trash2,
  Upload,
} from 'lucide-react';

import { api, ApiError, API_URL } from '@/lib/api-client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type UploadKind =
  | 'visura_cciaa'
  | 'visura_catastale'
  | 'documento_identita'
  | 'bolletta_pod'
  | 'altro';

export type ExtractionStatus =
  | 'pending'
  | 'success'
  | 'failed'
  | 'manual_required';

export interface PracticeUpload {
  id: string;
  practice_id: string;
  upload_kind: UploadKind;
  storage_path: string;
  original_name: string;
  mime_type: string;
  file_size_bytes: number;
  extraction_status: ExtractionStatus;
  extracted_data: Record<string, unknown>;
  confidence: number | null;
  extraction_error: string | null;
  extracted_at: string | null;
  applied_at: string | null;
  applied_targets: Record<string, string[]>;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Lookups
// ---------------------------------------------------------------------------

const KIND_LABEL: Record<UploadKind, string> = {
  visura_cciaa: 'Visura camerale (CCIAA)',
  visura_catastale: 'Visura catastale',
  documento_identita: "Documento d'identità",
  bolletta_pod: 'Bolletta elettrica (POD)',
  altro: 'Altro documento',
};

const KIND_HINT: Record<UploadKind, string> = {
  visura_cciaa:
    'Estrae ragione sociale, P.IVA, codice fiscale, n° CCIAA, sede legale, ATECO, legale rappresentante.',
  visura_catastale:
    'Estrae foglio, particella, subalterno, intestatario, categoria.',
  documento_identita:
    'Estrae nome, cognome, codice fiscale, residenza, data nascita.',
  bolletta_pod:
    'Estrae codice POD, distributore, potenza disponibile/impegnata, indirizzo fornitura.',
  altro:
    'Documento generico — i campi non vengono applicati automaticamente.',
};

const STATUS_LABEL: Record<ExtractionStatus, string> = {
  pending: 'Lettura in corso…',
  success: 'Lettura completata',
  manual_required: 'Verifica manuale',
  failed: 'Errore di lettura',
};

const STATUS_TONE: Record<ExtractionStatus, string> = {
  pending: 'bg-amber-100 text-amber-700',
  success: 'bg-emerald-100 text-emerald-700',
  manual_required: 'bg-amber-100 text-amber-700',
  failed: 'bg-rose-100 text-rose-700',
};

// Italian human labels for extracted_data keys we display in the
// suggestions list. Falling back to the raw key keeps unmapped fields
// visible (so the operator sees all extracted data, not a redacted view).
const FIELD_LABEL: Record<string, string> = {
  // Visura CCIAA
  ragione_sociale: 'Ragione sociale',
  forma_giuridica: 'Forma giuridica',
  partita_iva: 'P. IVA',
  codice_fiscale: 'Codice fiscale',
  numero_cciaa: 'N° CCIAA',
  sede_legale_indirizzo: 'Sede legale — indirizzo',
  sede_legale_cap: 'CAP',
  sede_legale_citta: 'Città',
  sede_legale_provincia: 'Provincia',
  codice_ateco: 'ATECO',
  legale_rappresentante_nome: 'Legale rappresentante — nome',
  legale_rappresentante_cognome: 'Legale rappresentante — cognome',
  legale_rappresentante_codice_fiscale: 'Legale rappresentante — CF',
  // Catastale
  foglio: 'Foglio',
  particella: 'Particella',
  subalterno: 'Subalterno',
  comune: 'Comune',
  provincia: 'Provincia',
  categoria_catastale: 'Categoria catastale',
  rendita_catastale: 'Rendita catastale',
  intestatario_nome_cognome: 'Intestatario',
  intestatario_codice_fiscale: 'Intestatario — CF',
  quota_possesso: 'Quota possesso',
  // ID
  tipo_documento: 'Tipo documento',
  numero_documento: 'Numero documento',
  nome: 'Nome',
  cognome: 'Cognome',
  data_nascita: 'Data di nascita',
  luogo_nascita: 'Luogo di nascita',
  residenza_indirizzo: 'Residenza',
  residenza_cap: 'CAP residenza',
  residenza_citta: 'Città residenza',
  residenza_provincia: 'Provincia residenza',
  data_rilascio: 'Data rilascio',
  data_scadenza: 'Data scadenza',
  // Bolletta POD
  pod: 'Codice POD',
  distributore: 'Distributore',
  tensione_alimentazione: 'Tensione',
  potenza_disponibile_kw: 'Potenza disponibile (kW)',
  potenza_impegnata_kw: 'Potenza impegnata (kW)',
  intestatario_nome: 'Intestatario — nome',
  intestatario_cognome: 'Intestatario — cognome',
  indirizzo_fornitura_via: 'Indirizzo fornitura',
  indirizzo_fornitura_cap: 'CAP fornitura',
  indirizzo_fornitura_citta: 'Città fornitura',
  indirizzo_fornitura_provincia: 'Provincia fornitura',
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function PracticeUploadsPanel({
  practiceId,
  onAfterApply,
}: {
  practiceId: string;
  /** Called after a successful apply so the parent can refetch the
   *  practice + missing-fields report.  No-op by default. */
  onAfterApply?: () => void;
}) {
  const [uploads, setUploads] = useState<PracticeUpload[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploadKind, setUploadKind] = useState<UploadKind>('visura_cciaa');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const refetch = useCallback(async () => {
    try {
      const rows = await api.get<PracticeUpload[]>(
        `/v1/practices/${practiceId}/uploads`,
      );
      setUploads(rows);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Errore caricamento.');
    } finally {
      setLoading(false);
    }
  }, [practiceId]);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  // Polling while any extraction is pending.
  const isPending = useMemo(
    () => uploads.some((u) => u.extraction_status === 'pending'),
    [uploads],
  );

  useEffect(() => {
    if (!isPending) return;
    const id = setInterval(() => void refetch(), 3000);
    return () => clearInterval(id);
  }, [isPending, refetch]);

  // ----- Upload handlers ----------------------------------------------------

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      // We intentionally serialize: in practice operators upload one
      // doc at a time, and serializing avoids burning Anthropic quota
      // in parallel calls if they accidentally drop 5 files at once.
      for (const file of Array.from(files)) {
        const fd = new FormData();
        fd.append('file', file);
        fd.append('upload_kind', uploadKind);
        await api.upload<PracticeUpload>(
          `/v1/practices/${practiceId}/uploads`,
          fd,
        );
      }
      await refetch();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Upload non riuscito.');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }

  function onDragEnter(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragActive(true);
  }

  function onDragLeave(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragActive(false);
  }

  function onDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragActive(false);
    void handleFiles(e.dataTransfer.files);
  }

  // ----- Per-row actions ----------------------------------------------------

  async function handleApply(upload: PracticeUpload) {
    setBusyId(upload.id);
    setError(null);
    try {
      await api.post(
        `/v1/practices/${practiceId}/uploads/${upload.id}/apply`,
        {},
      );
      await refetch();
      onAfterApply?.();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'Applicazione non riuscita.',
      );
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(upload: PracticeUpload) {
    if (
      !confirm(`Eliminare "${upload.original_name}"? L'operazione è irreversibile.`)
    )
      return;
    setBusyId(upload.id);
    try {
      await api.delete(
        `/v1/practices/${practiceId}/uploads/${upload.id}`,
      );
      await refetch();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Eliminazione fallita.');
    } finally {
      setBusyId(null);
    }
  }

  // --------------------------------------------------------------------------

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-widest text-on-surface-variant">
          Documenti caricati (OCR automatico)
        </h2>
        {uploads.length > 0 && (
          <span className="text-xs text-on-surface-muted">
            {uploads.length} document{uploads.length === 1 ? 'o' : 'i'}
          </span>
        )}
      </div>

      {/* Dropzone */}
      <div
        onDragEnter={onDragEnter}
        onDragOver={(e) => e.preventDefault()}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        className={`flex flex-col items-center gap-3 rounded-xl border-2 border-dashed p-6 transition-colors ${
          dragActive
            ? 'border-primary bg-primary/5'
            : 'border-on-surface/20 bg-white/40 hover:border-primary/40 hover:bg-white/60'
        }`}
      >
        <Upload size={28} className="text-on-surface-muted" />
        <div className="text-center">
          <p className="text-sm font-medium text-on-surface">
            Trascina qui i documenti del cliente o{' '}
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="text-primary underline-offset-2 hover:underline"
              disabled={uploading}
            >
              scegli un file
            </button>
          </p>
          <p className="mt-1 text-xs text-on-surface-variant">
            PDF, JPG, PNG, WEBP — max 10 MB. {KIND_HINT[uploadKind]}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs font-medium text-on-surface-variant">
            Tipo documento:
          </label>
          <select
            value={uploadKind}
            onChange={(e) => setUploadKind(e.target.value as UploadKind)}
            className="rounded-lg border border-on-surface/20 bg-white px-2 py-1 text-xs text-on-surface focus:border-primary focus:outline-none"
            disabled={uploading}
          >
            {(
              [
                'visura_cciaa',
                'visura_catastale',
                'documento_identita',
                'bolletta_pod',
                'altro',
              ] as UploadKind[]
            ).map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k]}
              </option>
            ))}
          </select>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          accept="image/jpeg,image/png,image/webp,application/pdf"
          multiple
          onChange={(e) => void handleFiles(e.target.files)}
          className="hidden"
        />
        {uploading && (
          <span className="flex items-center gap-1.5 text-xs text-amber-700">
            <Loader2 size={12} className="animate-spin" /> Caricamento…
          </span>
        )}
      </div>

      {error && (
        <div className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
          {error}
        </div>
      )}

      {/* Uploads list */}
      {loading ? (
        <div className="flex items-center gap-2 py-6 text-xs text-on-surface-muted">
          <Loader2 size={14} className="animate-spin" /> Caricamento elenco…
        </div>
      ) : uploads.length === 0 ? (
        <p className="text-xs text-on-surface-muted">
          Nessun documento caricato.
        </p>
      ) : (
        <ul className="space-y-3">
          {uploads.map((u) => (
            <UploadCard
              key={u.id}
              upload={u}
              practiceId={practiceId}
              busy={busyId === u.id}
              onApply={() => handleApply(u)}
              onDelete={() => handleDelete(u)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// UploadCard
// ---------------------------------------------------------------------------

function UploadCard({
  upload,
  practiceId,
  busy,
  onApply,
  onDelete,
}: {
  upload: PracticeUpload;
  practiceId: string;
  busy: boolean;
  onApply: () => void;
  onDelete: () => void;
}) {
  const tone = STATUS_TONE[upload.extraction_status];
  const statusLabel = STATUS_LABEL[upload.extraction_status];
  const canApply =
    (upload.extraction_status === 'success' ||
      upload.extraction_status === 'manual_required') &&
    !upload.applied_at &&
    upload.upload_kind !== 'altro';

  const fields = useMemo(() => {
    const data = upload.extracted_data ?? {};
    return Object.entries(data)
      .filter(([, v]) => v !== null && v !== undefined && v !== '')
      .map(([k, v]) => ({
        key: k,
        label: FIELD_LABEL[k] ?? k,
        value: typeof v === 'object' ? JSON.stringify(v) : String(v),
      }));
  }, [upload.extracted_data]);

  return (
    <li className="rounded-xl border border-on-surface/10 bg-white p-4">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex items-start gap-2.5 min-w-0">
          <FileText size={18} className="mt-0.5 shrink-0 text-on-surface-variant" />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-on-surface">
              {upload.original_name}
            </p>
            <p className="text-xs text-on-surface-variant">
              {KIND_LABEL[upload.upload_kind]} ·{' '}
              {(upload.file_size_bytes / 1024).toFixed(0)} KB
            </p>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <span
            className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium ${tone}`}
          >
            {statusLabel}
            {upload.confidence !== null && upload.extraction_status !== 'failed' &&
              ` · ${Math.round(upload.confidence * 100)}%`}
          </span>
          {upload.applied_at && (
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
              <Check size={10} /> Applicato
            </span>
          )}
        </div>
      </div>

      {/* Pending shimmer */}
      {upload.extraction_status === 'pending' && (
        <div className="mt-3 flex items-center gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
          <Loader2 size={12} className="animate-spin" />
          Lettura del documento in corso (può richiedere 5–15 secondi)…
        </div>
      )}

      {/* Error */}
      {upload.extraction_status === 'failed' && upload.extraction_error && (
        <div className="mt-3 flex items-start gap-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          <span>{upload.extraction_error}</span>
        </div>
      )}

      {/* Manual-required notice */}
      {upload.extraction_status === 'manual_required' && (
        <p className="mt-3 text-xs text-amber-700">
          Confidenza bassa — verifica i valori prima di applicarli.
        </p>
      )}

      {/* Extracted fields */}
      {fields.length > 0 && (
        <dl className="mt-3 grid gap-x-4 gap-y-1.5 sm:grid-cols-2">
          {fields.map((f) => (
            <div key={f.key} className="flex flex-col">
              <dt className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-muted">
                {f.label}
              </dt>
              <dd className="truncate text-xs text-on-surface" title={f.value}>
                {f.value}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {/* Applied-targets summary */}
      {upload.applied_at &&
        Object.keys(upload.applied_targets ?? {}).length > 0 && (
          <p className="mt-3 text-[11px] text-emerald-700">
            Campi scritti su:&nbsp;
            {Object.entries(upload.applied_targets)
              .map(([t, ks]) => `${t} (${(ks ?? []).length})`)
              .join(' · ')}
          </p>
        )}

      {/* Action row */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <a
          href={`${API_URL}/v1/practices/${practiceId}/uploads/${upload.id}/download`}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 rounded-lg border border-on-surface/10 px-3 py-1.5 text-xs font-medium text-on-surface-variant hover:bg-surface-container-lowest/40"
        >
          <Download size={12} /> Scarica
        </a>
        {canApply && (
          <button
            type="button"
            onClick={onApply}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <Check size={12} />
            )}
            Applica suggerimenti
          </button>
        )}
        <button
          type="button"
          onClick={onDelete}
          disabled={busy}
          className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-rose-200 px-3 py-1.5 text-xs font-medium text-rose-700 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Trash2 size={12} /> Elimina
        </button>
      </div>
    </li>
  );
}
