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
import { postProcessVideo, convertToGif } from './ffmpeg-service';
import { generateCrossfadeVideo } from './ffmpeg-crossfade';

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
   * Override the AI video-generation step (Kling). Used only when
   * VIDEO_RENDER_MODE=kling. Test seam + future model swap.
   */
  generateVideo?: (input: TransitionInput) => Promise<{ videoUrl: string; durationMs: number }>;
  /**
   * Override the FFmpeg crossfade step (test seam). Default mode.
   * Returns the work-dir path of the raw MP4 + its duration.
   */
  generateCrossfade?: (
    beforeImageUrl: string,
    afterImageUrl: string,
    workDir: string,
    outMp4Path: string,
  ) => Promise<{ durationS: number }>;
}

/** Motore di generazione del video di transizione.
 *  `crossfade` (default) = dissolvenza FFmpeg locale, costo zero.
 *  `kling` = animazione AI su Replicate (~€0,49/clip) — opt-in. */
const renderMode = (): 'crossfade' | 'kling' =>
  (process.env.VIDEO_RENDER_MODE ?? 'crossfade').toLowerCase() === 'kling'
    ? 'kling'
    : 'crossfade';

/**
 * Full pipeline:
 *   1. Produce a raw MP4 of the before→after transition:
 *      - crossfade (default): FFmpeg zoom + dissolve, zero API cost;
 *      - kling: hosted AI video model on Replicate (opt-in).
 *   2. ffmpeg → burn stats overlay on last ~1.5s + optimized GIF.
 *   3. Upload both files to `{bucket}/{outputPath}/transition.{mp4,gif}`.
 *   4. Return public URLs + total wall-clock duration.
 */
export const renderTransition = async (
  req: RenderRequest,
  deps: RenderDeps,
): Promise<RenderResult> => {
  const start = Date.now();

  // Working dir for the render + post-process steps.
  const workDir = await fs.mkdtemp(
    path.join(deps.tmpDir ?? os.tmpdir(), 'sl-render-'),
  );

  const stats = {
    kwp: req.kwp,
    yearlySavingsEur: req.yearlySavingsEur,
    paybackYears: req.paybackYears,
    tenantName: req.tenantName,
    brandPrimaryColor: req.brandPrimaryColor,
  };

  try {
    let mp4Path: string;
    let gifPath: string;

    if (renderMode() === 'kling') {
      // AI path (opt-in): hosted video model → download → overlay → GIF.
      const generator =
        deps.generateVideo ??
        ((input: TransitionInput) => generateTransitionVideo(input));
      const { videoUrl } = await generator(stripNonSchemaProps(req));
      ({ mp4Path, gifPath } = await postProcessVideo(
        videoUrl,
        workDir,
        stats,
        pickDuration(req.kwp),
      ));
    } else {
      // Default path: FFmpeg crossfade (zoom + dissolve) — no AI, no
      // per-clip API cost. The "Risparmio annuo" strip è ora bakeata
      // direttamente nell'after.png (vedi
      // `solar_rendering_service.bake_savings_strip`), quindi la
      // crossfade la rivela in modo naturale insieme ai pannelli — non
      // serve più passare per `overlayStatsOnVideo`. `stats` resta in
      // scope solo per il prossimo eventuale uso (es. tinta delle barre).
      const crossfade = deps.generateCrossfade ?? generateCrossfadeVideo;
      mp4Path = path.join(workDir, 'transition.mp4');
      gifPath = path.join(workDir, 'transition.gif');
      await crossfade(
        req.beforeImageUrl,
        req.afterImageUrl,
        workDir,
        mp4Path,
      );
      await convertToGif(mp4Path, gifPath);
    }

    // Upload to Supabase Storage.
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
