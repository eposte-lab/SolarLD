/**
 * Core render pipeline — bundle Remotion entry, render MP4 + GIF,
 * upload both to Supabase Storage, return the public URLs.
 *
 * Kept deliberately free of Express-isms so it can be unit-tested and
 * reused by the `render:example` CLI script.
 */
import path from 'node:path';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import crypto from 'node:crypto';

import { bundle } from '@remotion/bundler';
import { renderMedia, selectComposition } from '@remotion/renderer';
import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import { z } from 'zod';

import { solarTransitionSchema } from './compositions/SolarTransition';

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------

/**
 * The full render request accepted by POST /render. On top of the
 * Remotion composition props we need: a destination folder in the
 * Supabase bucket (`outputPath`, e.g. `{tenantId}/{leadId}`) and a
 * bucket name (defaults to `renderings`).
 */
export const renderRequestSchema = solarTransitionSchema.extend({
  outputPath: z
    .string()
    .min(1)
    .refine((p) => !p.startsWith('/') && !p.includes('..'), {
      message: 'outputPath must be a relative, non-traversing path',
    }),
  bucket: z.string().min(1).default('renderings'),
});

export type RenderRequest = z.infer<typeof renderRequestSchema>;

export interface RenderResult {
  mp4Url: string;
  gifUrl: string;
  durationMs: number;
}

// ---------------------------------------------------------------------------
// Bundle cache — bundling takes ~1s cold so we keep the result per process.
// ---------------------------------------------------------------------------

let cachedBundlePromise: Promise<string> | null = null;

/**
 * Bundle the Remotion entry once per process and re-use the output
 * directory. Exported so tests can swap it for a stub.
 */
export const getOrBuildBundle = async (entry?: string): Promise<string> => {
  if (cachedBundlePromise) return cachedBundlePromise;
  const entryPoint = entry ?? path.resolve(__dirname, 'remotion.tsx');
  cachedBundlePromise = bundle({ entryPoint });
  return cachedBundlePromise;
};

/** Reset — used by tests between runs. */
export const _resetBundleCache = (): void => {
  cachedBundlePromise = null;
};

// ---------------------------------------------------------------------------
// Public entry
// ---------------------------------------------------------------------------

export interface RenderDeps {
  /** Lazy Supabase client factory so tests can inject a mock. */
  supabase: SupabaseClient;
  /** Override for the workspace tmp dir (tests). */
  tmpDir?: string;
  /** Inject a pre-built bundle location (tests). */
  bundleLocation?: string;
}

/**
 * Full pipeline:
 *   1. Ensure a Remotion bundle exists (cached).
 *   2. `selectComposition` to resolve fps/width/height.
 *   3. `renderMedia` → mp4 in tmp dir.
 *   4. `renderMedia` again with codec='gif' at lower fps/size → gif.
 *   5. Upload both files to `{bucket}/{outputPath}/transition.{mp4,gif}`.
 *   6. Return public URLs + wall-clock duration.
 *
 * On any error mid-way the tmp files are cleaned up, but partially-
 * uploaded assets are NOT deleted — the caller (Python CreativeAgent)
 * retries with the same deterministic path so the next run overwrites.
 */
export const renderTransition = async (
  req: RenderRequest,
  deps: RenderDeps,
): Promise<RenderResult> => {
  const start = Date.now();
  const bundleLocation = deps.bundleLocation ?? (await getOrBuildBundle());

  const composition = await selectComposition({
    serveUrl: bundleLocation,
    id: 'SolarTransition',
    inputProps: stripNonSchemaProps(req),
  });

  const workDir = await fs.mkdtemp(
    path.join(deps.tmpDir ?? os.tmpdir(), 'sl-render-'),
  );
  const mp4Path = path.join(workDir, 'transition.mp4');
  const gifPath = path.join(workDir, 'transition.gif');

  try {
    // 1) MP4 — full quality, full res, 30fps.
    await renderMedia({
      composition,
      serveUrl: bundleLocation,
      codec: 'h264',
      outputLocation: mp4Path,
      inputProps: stripNonSchemaProps(req),
      imageFormat: 'jpeg',
      crf: 22,
    });

    // 2) GIF — same composition, half resolution for file size,
    // same frame count so timing matches the MP4 exactly.
    await renderMedia({
      composition: {
        ...composition,
        width: Math.round(composition.width / 2),
        height: Math.round(composition.height / 2),
      },
      serveUrl: bundleLocation,
      codec: 'gif',
      outputLocation: gifPath,
      inputProps: stripNonSchemaProps(req),
    });

    // 3) Upload to Supabase Storage
    const mp4Bytes = await fs.readFile(mp4Path);
    const gifBytes = await fs.readFile(gifPath);

    const mp4Url = await uploadBytes(
      deps.supabase,
      req.bucket,
      joinPath(req.outputPath, 'transition.mp4'),
      mp4Bytes,
      'video/mp4',
    );
    const gifUrl = await uploadBytes(
      deps.supabase,
      req.bucket,
      joinPath(req.outputPath, 'transition.gif'),
      gifBytes,
      'image/gif',
    );

    return {
      mp4Url,
      gifUrl,
      durationMs: Date.now() - start,
    };
  } finally {
    // Leave the tmp files in place on error for debugging in dev, but
    // always try to tidy up on the happy path. `rm -rf` semantics.
    fs.rm(workDir, { recursive: true, force: true }).catch(() => {
      /* non-fatal */
    });
  }
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Remotion's `inputProps` must match `solarTransitionSchema` exactly
 * (unknown keys like `outputPath` would fail the zod refine). This
 * helper drops our sidecar-only fields.
 */
export const stripNonSchemaProps = (req: RenderRequest) => {
  // `bucket` and `outputPath` are for the sidecar, not the composition.
  const { outputPath: _outputPath, bucket: _bucket, ...compositionProps } = req;
  return compositionProps;
};

export const joinPath = (...segments: string[]): string => {
  return segments
    .map((s) => s.replace(/^\/+|\/+$/g, ''))
    .filter(Boolean)
    .join('/');
};

/** Upload bytes to Supabase Storage and return the public URL. */
export const uploadBytes = async (
  supabase: SupabaseClient,
  bucket: string,
  storagePath: string,
  data: Buffer,
  contentType: string,
): Promise<string> => {
  const { error } = await supabase.storage.from(bucket).upload(storagePath, data, {
    contentType,
    upsert: true,
  });
  if (error) {
    throw new Error(`supabase upload failed: ${error.message}`);
  }
  const { data: pub } = supabase.storage.from(bucket).getPublicUrl(storagePath);
  return pub.publicUrl;
};

/** Default Supabase client from environment. */
export const buildSupabaseClient = (): SupabaseClient => {
  const url = process.env.SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) {
    throw new Error(
      'SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must both be set',
    );
  }
  return createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
};

/** Used by tests to get a unique outputPath without colliding. */
export const randomSuffix = (): string => crypto.randomBytes(4).toString('hex');
