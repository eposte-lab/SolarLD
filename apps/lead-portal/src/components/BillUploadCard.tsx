'use client';

/**
 * BillUploadCard — bolletta upload + manual entry + OCR confirmation.
 *
 * Sprint 8 Fase B.4. Three states:
 *
 *   1. ``idle``   — drop zone + "Carica bolletta" button
 *   2. ``uploading`` — spinner while POST /bolletta is in flight
 *   3. ``review`` — show extracted values, allow inline edit when
 *                   ``status='manual_required'`` (low OCR confidence
 *                   or PDF / parse failure)
 *   4. ``saved``  — confirmation banner, then call ``onSaved`` so the
 *                   parent re-fetches the savings compare panel
 *
 * The card never blocks the rest of the page: errors land in the
 * card's own banner instead of throwing toasts. We trust the user
 * to retry if needed.
 *
 * Server endpoint contract (see apps/api/src/routes/public.py):
 *   POST /v1/public/lead/{slug}/bolletta        (multipart, file)
 *   POST /v1/public/lead/{slug}/bolletta/manual (json: kwh+eur+upload_id?)
 */

import { Loader2, Upload } from 'lucide-react';
import { useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from 'react';

import { API_URL } from '@/lib/api';

type Props = {
  slug: string;
  brandColor: string;
  /** Called once the user confirms a value (manual or OCR). */
  onSaved?: () => void;
};

type Status = 'idle' | 'uploading' | 'review' | 'saved';

type UploadResponse = {
  upload_id: string;
  status: 'ok' | 'manual_required';
  source: 'upload_ocr' | 'upload_manual' | 'manual_only';
  ocr_kwh_yearly?: number | null;
  ocr_eur_yearly?: number | null;
  ocr_confidence?: number | null;
  ocr_provider_name?: string | null;
  ocr_error?: string | null;
  manual_kwh_yearly?: number | null;
  manual_eur_yearly?: number | null;
};

const ACCEPT_MIME =
  'image/jpeg,image/png,image/webp,application/pdf';
const MAX_BYTES = 10 * 1024 * 1024;

export function BillUploadCard({ slug, brandColor, onSaved }: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<Status>('idle');
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<UploadResponse | null>(null);
  const [draftKwh, setDraftKwh] = useState<string>('');
  const [draftEur, setDraftEur] = useState<string>('');
  const [dragOver, setDragOver] = useState(false);

  function reset() {
    setStatus('idle');
    setError(null);
    setResponse(null);
    setDraftKwh('');
    setDraftEur('');
  }

  async function uploadFile(file: File) {
    setError(null);
    if (file.size > MAX_BYTES) {
      setError('Il file è troppo grande. Massimo 10 MB.');
      return;
    }
    setStatus('uploading');
    const fd = new FormData();
    fd.append('file', file);
    try {
      const res = await fetch(
        `${API_URL}/v1/public/lead/${encodeURIComponent(slug)}/bolletta`,
        { method: 'POST', body: fd },
      );
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
      }
      const data: UploadResponse = await res.json();
      setResponse(data);
      const kwh = data.ocr_kwh_yearly ?? data.manual_kwh_yearly ?? null;
      const eur = data.ocr_eur_yearly ?? data.manual_eur_yearly ?? null;
      setDraftKwh(kwh != null ? String(Math.round(kwh)) : '');
      setDraftEur(eur != null ? String(Math.round(eur)) : '');
      setStatus(data.status === 'ok' ? 'saved' : 'review');
      if (data.status === 'ok') onSaved?.();
    } catch (err) {
      setError(
        err instanceof Error
          ? `Caricamento non riuscito: ${err.message}`
          : 'Caricamento non riuscito',
      );
      setStatus('idle');
    }
  }

  async function submitManual(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const kwh = Number(draftKwh.replace(',', '.'));
    const eur = Number(draftEur.replace(',', '.'));
    if (!Number.isFinite(kwh) || kwh <= 0) {
      setError('Inserisci un consumo annuo valido in kWh.');
      return;
    }
    if (!Number.isFinite(eur) || eur <= 0) {
      setError('Inserisci una spesa annua valida in €.');
      return;
    }
    setStatus('uploading');
    try {
      const res = await fetch(
        `${API_URL}/v1/public/lead/${encodeURIComponent(slug)}/bolletta/manual`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            kwh_yearly: kwh,
            eur_yearly: eur,
            upload_id: response?.upload_id ?? null,
          }),
        },
      );
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
      }
      setStatus('saved');
      onSaved?.();
    } catch (err) {
      setError(
        err instanceof Error
          ? `Salvataggio non riuscito: ${err.message}`
          : 'Salvataggio non riuscito',
      );
      setStatus('review');
    }
  }

  function onPickFile() {
    fileInputRef.current?.click();
  }

  function onFileChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) void uploadFile(file);
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) void uploadFile(file);
  }

  return (
    <section
      className="bento-glass p-6 md:p-8"
      data-portal-cta="bolletta"
      aria-labelledby="bolletta-heading"
    >
      <p className="editorial-eyebrow">Risparmio personalizzato</p>
      <h2
        id="bolletta-heading"
        className="mt-2 font-headline text-2xl font-semibold tracking-tighter text-on-surface md:text-3xl"
      >
        Carica la tua bolletta — vedi quanto risparmieresti davvero
      </h2>
      <p className="mt-2 text-sm text-on-surface-variant md:text-base">
        Foto o PDF della bolletta luce. Estraiamo il consumo annuo e
        lo confrontiamo con la tua proposta. I dati restano riservati.
      </p>

      {error ? (
        <p className="mt-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      ) : null}

      {status === 'idle' || status === 'uploading' ? (
        <div className="mt-6">
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className={
              'flex flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-colors ' +
              (dragOver
                ? 'border-primary bg-primary-soft'
                : 'border-outline-variant bg-surface-container-low')
            }
          >
            <Upload
              className="h-8 w-8 text-on-surface-variant"
              aria-hidden
            />
            <p className="mt-3 text-sm font-medium text-on-surface">
              Trascina qui la bolletta oppure
            </p>
            <button
              type="button"
              onClick={onPickFile}
              disabled={status === 'uploading'}
              className="mt-3 inline-flex items-center gap-2 rounded-full px-5 py-2 text-sm font-semibold text-white shadow-sm transition disabled:opacity-50"
              style={{ backgroundColor: brandColor }}
            >
              {status === 'uploading' ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  Analisi in corso…
                </>
              ) : (
                'Scegli file'
              )}
            </button>
            <p className="mt-3 text-xs text-on-surface-muted">
              JPG, PNG, WEBP o PDF · max 10 MB
            </p>
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPT_MIME}
              className="hidden"
              onChange={onFileChange}
            />
          </div>

          <button
            type="button"
            onClick={() => {
              setResponse(null);
              setStatus('review');
            }}
            className="mt-4 text-sm font-medium text-on-surface-variant underline-offset-2 hover:underline"
          >
            Non hai la bolletta a portata di mano? Inserisci i numeri a mano
          </button>
        </div>
      ) : null}

      {status === 'review' ? (
        <form onSubmit={submitManual} className="mt-6 grid gap-4">
          {response?.ocr_kwh_yearly != null ? (
            <p className="rounded-lg bg-surface-container-high px-4 py-3 text-xs text-on-surface-variant">
              Abbiamo letto questi numeri dalla bolletta — controlla che
              corrispondano al totale annuo, eventualmente correggi.
              {response.ocr_confidence != null
                ? ` Confidenza ${Math.round(
                    response.ocr_confidence * 100,
                  )}%.`
                : ''}
            </p>
          ) : null}
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-sm font-medium text-on-surface-variant">
              Consumo annuo (kWh)
              <input
                value={draftKwh}
                onChange={(e) => setDraftKwh(e.target.value)}
                inputMode="numeric"
                placeholder="es. 3.500"
                className="rounded-lg border border-outline-variant bg-surface-container px-3 py-2 text-base text-on-surface focus:border-primary focus:outline-none"
                required
              />
            </label>
            <label className="flex flex-col gap-1 text-sm font-medium text-on-surface-variant">
              Spesa annua (€)
              <input
                value={draftEur}
                onChange={(e) => setDraftEur(e.target.value)}
                inputMode="numeric"
                placeholder="es. 1.100"
                className="rounded-lg border border-outline-variant bg-surface-container px-3 py-2 text-base text-on-surface focus:border-primary focus:outline-none"
                required
              />
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="submit"
              disabled={status === 'uploading' as Status}
              className="inline-flex items-center gap-2 rounded-full px-5 py-2 text-sm font-semibold text-white shadow-sm transition disabled:opacity-50"
              style={{ backgroundColor: brandColor }}
            >
              Salva e calcola risparmio
            </button>
            <button
              type="button"
              onClick={reset}
              className="text-sm font-medium text-on-surface-variant underline-offset-2 hover:underline"
            >
              Annulla
            </button>
          </div>
        </form>
      ) : null}

      {status === 'saved' ? (
        <div className="mt-6 rounded-2xl bg-primary-soft px-5 py-4">
          <p className="font-headline text-base font-semibold text-on-surface">
            Bolletta registrata. Aggiorniamo il confronto qui sotto.
          </p>
          <button
            type="button"
            onClick={reset}
            className="mt-2 text-xs font-medium text-on-surface-variant underline-offset-2 hover:underline"
          >
            Carica un&apos;altra bolletta
          </button>
        </div>
      ) : null}
    </section>
  );
}
