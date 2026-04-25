/**
 * Replicate client — turns a "before rooftop" still + a descriptive
 * prompt into a short MP4 of solar panels gradually appearing on the
 * roof, animated by a hosted video model.
 *
 * Why hosted: building this in-house with Three.js (the previous
 * approach) produced visibly bad results — geometric cube artifacts,
 * panels embedded in a rectangular volume instead of laid flat on the
 * roof, low-fps stutter, no ambient animation. A keyframe-conditioned
 * video model handles geometry + lighting + ambient motion (cars,
 * shadows) in one shot.
 *
 * Default model: kwaivgi/kling-v1.6-standard — mid-tier price (~$0.30
 * per 5s clip), supports start_image conditioning + descriptive
 * prompt, 1:1 aspect ratio (matches our 1080×1080 source frames).
 *
 * Override at runtime with REPLICATE_VIDEO_MODEL — we keep the model
 * name in env so swapping engines (Luma Ray, Pika, MiniMax, etc.)
 * needs zero code change as long as the new model accepts the same
 * `start_image` + `prompt` + `duration` + `aspect_ratio` inputs.
 */
import Replicate from 'replicate';

import type { TransitionInput } from './schema';

const DEFAULT_MODEL = 'kwaivgi/kling-v1.6-standard';

/**
 * Prompt master — describes what the model should animate from the
 * before-frame onward. Tuned for Italian commercial rooftops (the
 * primary target segment) but generic enough that the model handles
 * residential roofs too.
 *
 * Things deliberately NOT in the prompt:
 *   - panel geometry (model infers from the after-frame conditioning
 *     OR from the implicit "solar panels" semantic)
 *   - text/logos (we add overlays in ffmpeg post-processing where we
 *     control the typography, instead of asking the model to invent
 *     letters that always look weird in generative video)
 */
const DEFAULT_PROMPT =
  'Photo-realistic aerial timelapse: monocrystalline solar panels are gradually installed across the rooftop of this building, panel by panel, in a smooth left-to-right reveal. Soft ambient daylight, subtle shadow movement, parked cars and surroundings remain mostly still. No camera motion, no zoom, fixed top-down framing. Photorealistic, high detail, no text, no logos, no watermarks.';

const NEGATIVE_PROMPT =
  'low quality, blurry, distorted geometry, warped roof, floating panels, text, captions, logos, watermarks, cartoon, illustration, neon colors, weird lighting';

export interface VideoGenerationResult {
  /** Public, time-limited Replicate URL of the produced MP4. */
  videoUrl: string;
  /** Wall-clock duration of the Replicate call (ms). */
  durationMs: number;
  /** Model identifier we ended up calling. */
  model: string;
}

export interface ReplicateClient {
  run(model: string, options: { input: Record<string, unknown> }): Promise<unknown>;
}

/** Lazy-initialized Replicate client (so tests can inject a fake). */
let cachedClient: ReplicateClient | null = null;
export const buildReplicateClient = (): ReplicateClient => {
  if (cachedClient) return cachedClient;
  const auth = process.env.REPLICATE_API_TOKEN;
  if (!auth) {
    throw new Error('REPLICATE_API_TOKEN must be set');
  }
  cachedClient = new Replicate({ auth }) as ReplicateClient;
  return cachedClient;
};

/** Reset — used by tests between runs. */
export const _resetReplicateCache = (): void => {
  cachedClient = null;
};

/**
 * Pick a clip duration based on rough building scale (kWp). Bigger
 * roofs deserve a slightly longer reveal so the eye has time to
 * register the spread. Capped at 5s to keep cost / file size sane.
 */
export const pickDuration = (kwp: number): 5 | 10 => {
  // Kling 1.6 standard accepts 5 or 10 seconds. 5s is plenty for any
  // reasonable inbox; 10s would push GIF size + Replicate cost ~2x.
  return kwp >= 100 ? 10 : 5;
};

export interface GenerateVideoDeps {
  client?: ReplicateClient;
  /** Override the model identifier (test seam, also REPLICATE_VIDEO_MODEL). */
  model?: string;
  /** Override the prompt (test seam, future per-tenant customization). */
  prompt?: string;
}

/**
 * Call the configured video model with `input.beforeImageUrl` as the
 * conditioning frame and a fixed prompt describing the panel reveal.
 *
 * Returns the public Replicate output URL — the caller is responsible
 * for downloading the MP4 and re-uploading to long-term storage
 * (Replicate URLs expire after ~1h).
 */
export const generateTransitionVideo = async (
  input: TransitionInput,
  deps: GenerateVideoDeps = {},
): Promise<VideoGenerationResult> => {
  const start = Date.now();
  const client = deps.client ?? buildReplicateClient();
  const model = deps.model ?? process.env.REPLICATE_VIDEO_MODEL ?? DEFAULT_MODEL;
  const prompt = deps.prompt ?? DEFAULT_PROMPT;

  const replicateInput: Record<string, unknown> = {
    prompt,
    negative_prompt: NEGATIVE_PROMPT,
    start_image: input.beforeImageUrl,
    duration: pickDuration(input.kwp),
    aspect_ratio: '1:1',
    cfg_scale: 0.5,
  };

  // `replicate.run` resolves the model version automatically when a
  // bare `owner/name` is passed (no `:version` hash needed).
  const output = (await client.run(model, { input: replicateInput })) as unknown;
  const videoUrl = extractVideoUrl(output);

  return {
    videoUrl,
    durationMs: Date.now() - start,
    model,
  };
};

/**
 * Different Replicate models return their output in different shapes:
 *   - kwaivgi/kling-*  → string URL
 *   - some return [url] (array)
 *   - some return { output: url } (object)
 *   - some return a stream-like object with `.url()`
 *
 * Be permissive on input but strict on the result type.
 */
export const extractVideoUrl = (output: unknown): string => {
  if (typeof output === 'string') return output;
  if (Array.isArray(output) && typeof output[0] === 'string') return output[0];
  if (output && typeof output === 'object') {
    const obj = output as Record<string, unknown>;
    if (typeof obj.output === 'string') return obj.output;
    if (Array.isArray(obj.output) && typeof obj.output[0] === 'string') {
      return obj.output[0];
    }
    if (typeof obj.url === 'function') {
      const u = (obj.url as () => unknown)();
      if (typeof u === 'string') return u;
      if (u && typeof (u as URL).toString === 'function') return String(u);
    }
  }
  throw new Error(
    `replicate output did not contain a video URL: ${JSON.stringify(output).slice(0, 300)}`,
  );
};
