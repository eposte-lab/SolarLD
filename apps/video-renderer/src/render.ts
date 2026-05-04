/**
 * Core render pipeline — replaces the old Remotion + Three.js path.
 *
 * Why this rewrite: the in-house 3-D Three.js render produced visibly
 * bad geometry (cube-like dome with embedded panels), low fps, and no
 * realistic lighting. We now delegate the actual video synthesis to a
 * hosted image-to-video model (Replicate / Kling 1.6) which:
 *   - takes the existing "before rooftop" still as start_image
 *   - takes a descriptive prompt for the panel-reveal animation
 *   - returns a photo-realistic MP4 with ambient motion (subtle shadow
 *     drift, parked-car glints, etc.)
 *
 * This module then ffmpeg-post-processes the model output:
 *   - burns a stats card (kWp / yearly savings / payback) over the
 *     last ~1.5s
 *   - emits a Gmail-friendly GIF (~2-4 MB) for inline email embedding
 *
 * The HTTP contract (POST /render body shape, response shape) is kept
 * identical so the Python CreativeAgent client doesn't change.
 */
import path from 'node:path';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import crypto from 'node:crypto';

import { createClient, type SupabaseClient } from '@supabase/supabase-js';

import {
  type RenderRequest,
  type RenderResult,
  type TransitionInput,
  renderRequestSchema,
} from './schema';
import { generateTransitionVideo, pickDuration } from './replicate-service';
import { postProcessVideo } from './ffmpeg-service';

// Re-export so existing imports (server.ts, tests) don't have to chase
// the schema down a level.
export { renderRequestSchema };
export type { RenderRequest, RenderResult };

// ---------------------------------------------------------------------------
// Public entry
// ---------------------------------------------------------------------------

export interface RenderDeps {
  /** Lazy Supabase client factory so tests can inject a mock. */
  supabase: SupabaseClient;
  /** Override for the workspace tmp dir (tests). */
  tmpDir?: string;
  /**
   * Override the video-generation step (tests + future model swap).
   * Default = call Replicate.
   */
  generateVideo?: (input: TransitionInput) => Promise<{ videoUrl: string; durationMs: number }>;
}

/**
 * Full pipeline:
 *   1. Call the Replicate video model with `beforeImageUrl` + prompt.
 *   2. Download the resulting MP4 to a tmp work dir.
 *   3. ffmpeg → burn stats overlay on last ~1.5s.
 *   4. ffmpeg → produce optimized GIF (480×480, 12fps).
 *   5. Upload both files to `{bucket}/{outputPath}/transition.{mp4,gif}`.
 *   6. Return public URLs + total wall-clock duration.
 */
export const renderTransition = async (
  req: RenderRequest,
  deps: RenderDeps,
): Promise<RenderResult> => {
  const start = Date.now();

  // 1) Hosted video generation (default = Replicate).
  const generator =
    deps.generateVideo ??
    ((input: TransitionInput) => generateTransitionVideo(input));
  const { videoUrl } = await generator(stripNonSchemaProps(req));

  // 2) Working dir for the post-process step.
  const workDir = await fs.mkdtemp(
    path.join(deps.tmpDir ?? os.tmpdir(), 'sl-render-'),
  );

  try {
    // 3) Download → overlay → GIF.
    const { mp4Path, gifPath } = await postProcessVideo(
      videoUrl,
      workDir,
      {
        kwp: req.kwp,
        yearlySavingsEur: req.yearlySavingsEur,
        paybackYears: req.paybackYears,
        tenantName: req.tenantName,
        brandPrimaryColor: req.brandPrimaryColor,
      },
      pickDuration(req.kwp),
    );

    // 4) Upload to Supabase Storage.
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
    // Best-effort cleanup; non-fatal if it fails.
    fs.rm(workDir, { recursive: true, force: true }).catch(() => {
      /* non-fatal */
    });
  }
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Drop the sidecar-only fields (`outputPath`, `bucket`) before passing
 * the request through to the video-generation layer. Matches the
 * Remotion-era signature so existing tests keep working.
 */
export const stripNonSchemaProps = (req: RenderRequest): TransitionInput => {
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
    // The video-renderer never subscribes to Realtime channels — disable it
    // to avoid Node < 22 WebSocket bootstrap errors on startup.
    realtime: { params: { eventsPerSecond: 0 } },
  });
};

/** Used by tests / the example CLI to get a unique outputPath. */
export const randomSuffix = (): string => crypto.randomBytes(4).toString('hex');
