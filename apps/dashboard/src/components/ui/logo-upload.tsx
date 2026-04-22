'use client';

/**
 * LogoUpload — file picker that uploads a PNG/JPG/WebP/SVG directly to
 * the `branding` Supabase Storage bucket and returns the public URL.
 *
 * Behavior:
 *   - Validates client-side: MIME in whitelist, size ≤ 5 MB.
 *   - Writes to `{tenantId}/logo-{timestamp}.{ext}` so each upload is
 *     content-addressable and we sidestep CDN cache invalidation.
 *   - RLS: storage policy in migration 0012+0040 allows INSERT /
 *     UPDATE / DELETE only when the first folder matches the caller's
 *     tenant. `createBrowserClient()` uses the user JWT, so the
 *     enforcement happens server-side automatically.
 *   - On success calls `onChange(publicUrl)` so the parent persists
 *     the URL through the existing `PATCH /v1/tenants/me` flow.
 *   - "Rimuovi" button clears the URL locally; the file in storage is
 *     left orphaned (cheap; 5 MB cap, optional cleanup cron later).
 */

import { useRef, useState } from 'react';

import { createBrowserClient } from '@/lib/supabase/client';
import { cn } from '@/lib/utils';

const ALLOWED = new Set([
  'image/png',
  'image/jpeg',
  'image/webp',
  'image/svg+xml',
]);
const MAX_BYTES = 5 * 1024 * 1024;

interface LogoUploadProps {
  tenantId: string;
  value: string;
  onChange: (url: string) => void;
}

function extFromMime(mime: string): string {
  if (mime === 'image/png') return 'png';
  if (mime === 'image/jpeg') return 'jpg';
  if (mime === 'image/webp') return 'webp';
  if (mime === 'image/svg+xml') return 'svg';
  return 'bin';
}

export function LogoUpload({ tenantId, value, onChange }: LogoUploadProps) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFile(file: File) {
    setError(null);

    if (!ALLOWED.has(file.type)) {
      setError('Formato non supportato. Usa PNG, JPG, WebP o SVG.');
      return;
    }
    if (file.size > MAX_BYTES) {
      setError(
        `File troppo grande (${(file.size / 1024 / 1024).toFixed(1)} MB). Massimo 5 MB.`,
      );
      return;
    }

    setUploading(true);
    try {
      const sb = createBrowserClient();
      // Content-addressable path: timestamp in filename forces a new
      // URL on every upload, so CDN cache on the old file is irrelevant.
      const ext = extFromMime(file.type);
      const path = `${tenantId}/logo-${Date.now()}.${ext}`;

      const { error: upErr } = await sb.storage
        .from('branding')
        .upload(path, file, {
          contentType: file.type,
          cacheControl: '3600',
          upsert: false,
        });
      if (upErr) throw upErr;

      const { data } = sb.storage.from('branding').getPublicUrl(path);
      if (!data?.publicUrl) throw new Error('Public URL missing');

      onChange(data.publicUrl);
    } catch (e) {
      const msg = (e as Error).message || 'Upload fallito.';
      setError(msg);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  }

  return (
    <div>
      <input
        ref={fileRef}
        type="file"
        accept="image/png,image/jpeg,image/webp,image/svg+xml"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void handleFile(f);
        }}
      />

      {value ? (
        <div className="mt-2 flex items-center gap-3 rounded-lg border border-outline-variant/40 bg-surface-container-lowest p-3">
          <div className="flex h-14 w-28 shrink-0 items-center justify-center rounded-md bg-surface-container px-2">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={value}
              alt="Logo caricato"
              className="max-h-10 max-w-[100px] object-contain"
              onError={(e) => (e.currentTarget.style.display = 'none')}
            />
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs text-on-surface-variant">
              {value.split('/').slice(-1)[0] || 'logo'}
            </p>
            <div className="mt-1 flex gap-3 text-xs">
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                disabled={uploading}
                className="font-semibold text-primary hover:underline disabled:opacity-50"
              >
                Sostituisci
              </button>
              <button
                type="button"
                onClick={() => onChange('')}
                disabled={uploading}
                className="font-semibold text-on-surface-variant hover:text-error disabled:opacity-50"
              >
                Rimuovi
              </button>
            </div>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          className={cn(
            'mt-2 flex w-full flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed border-outline-variant/50 bg-surface-container-lowest px-4 py-6 text-center transition-colors',
            'hover:border-primary/60 hover:bg-primary-container/10',
            uploading && 'cursor-wait opacity-60',
          )}
        >
          <span className="text-sm font-semibold text-on-surface">
            {uploading ? 'Caricamento…' : 'Carica logo'}
          </span>
          <span className="text-xs text-on-surface-variant">
            PNG · JPG · WebP · SVG — max 5 MB
          </span>
        </button>
      )}

      {error && <p className="mt-2 text-xs text-error">{error}</p>}
    </div>
  );
}
