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
 * Default model: kwaivgi/kling-v1.6-pro (~$0.49 per 5s clip).
 *
 * Why Pro instead of Standard: Pro accepts BOTH `start_image` (before
 * frame) AND `end_image` (after frame) as conditioning. We feed it our
 * PIL-rendered `after.png` — which has the exact panel placement from
 * the Google Solar API's per-panel lat/lng — so the model is forced
 * to converge on the correct geometry instead of inventing where the
 * panels go (which is what produced the "panels on the lawn" / "water
 * pouring on the roof" look in the previous Standard runs).
 *
 * Override at runtime with REPLICATE_VIDEO_MODEL — we keep the model
 * name in env so swapping engines (Luma Ray, Runway Gen-3, etc.)
 * needs zero code change as long as the new model accepts the same
 * `start_image` + `end_image` + `prompt` inputs.
 */
import Replicate from 'replicate';

import type { TransitionInput } from './schema';

const DEFAULT_MODEL = 'kwaivgi/kling-v1.6-pro';

/**
 * Prompt master — describes the TRANSITION from start to end frame.
 *
 * Because we condition on both endpoints (start = bare roof, end =
 * roof with all panels in their exact positions), the prompt only
 * needs to describe HOW the model gets from one to the other. The
 * "where" is already pinned by `end_image`.
 *
 * Animation style: pannello-per-pannello in posizione.
 * Tenant feedback (Apr 2026): the previous "one row at a time, edge
 * to edge" wording produced a conveyor-belt look — entire rows
 * dropping in from above the frame, sliding sideways, etc. We want
 * panels to MATERIALISE directly in their final position (no descent,
 * no slide, no rotation) at a rapid cadence so the build-up reads as
 * "the array assembles itself" rather than "panels are being trucked
 * in from off-screen". This is closer to a stop-motion pop-in than a
 * physical install animation.
 *
 * Things deliberately NOT in the prompt:
 *   - panel geometry, count, position (covered by end_image)
 *   - text/logos (added in ffmpeg overlay post-processing where we
 *     control typography)
 */
const DEFAULT_PROMPT =
  'Photo-realistic aerial timelapse showing solar panels appearing one at a time directly on the visible rooftop, each panel materialising in place at its final position with a brief shimmer or fade-in. The panels do NOT fall from above, do NOT slide in from the sides, do NOT descend or translate — each individual panel simply becomes visible in the exact spot where it belongs, in rapid sequence (multiple panels per second), so the array fills in pixel by pixel until the rooftop is complete. The order is non-linear — panels light up across different parts of the roof, not row by row. Subtle ambient motion enriches the rest of the scene: any visible cars on nearby streets drift forward smoothly, tree foliage rustles gently in the breeze, and soft cloud shadows drift across the ground over the duration of the clip. The rest of the static scene (ground, vegetation, neighbouring buildings, vehicles parked) remains stable — panels appear ONLY on the building rooftop, never on the ground, lawn, driveway or surroundings. Fixed top-down camera, no zoom, no pan, no rotation, no tilt. Soft natural daylight, realistic shadows, photorealistic, sharp focus, professional aerial cinematography.';

const NEGATIVE_PROMPT =
  'panels falling from above, panels descending, panels dropping, panels sliding in, panels translating into position, panels rotating into place, conveyor belt motion, rows of panels appearing simultaneously, rows being installed one at a time, edge-to-edge sweeping reveal, panels arriving from off-screen, panels carried by hands or machines, construction workers, cranes, drones, water, liquid, pool, flood, wet surface, reflection of water, panels on ground, panels on grass, panels on pavement, panels floating, panels on cars, panels on trees, panels on neighbouring buildings, low quality, blurry, distorted geometry, warped roof, melting, morphing, dissolving, fade between two whole frames, cross-fade, opacity blending of full image, ghosting, double exposure, text, captions, logos, watermarks, cartoon, illustration, neon colors, weird lighting, camera motion, zoom, pan, rotation, dolly, tilt';

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
    // end_image: forces the generated clip to land on our PIL-rendered
    // after-frame which has the exact panel placement from the Solar
    // API. Without this anchor the model freestyles the final state
    // and panels end up on the lawn / driveway / wherever the prior
    // distribution biases it (Standard model failure mode).
    //
    // Supported by kling-v1.6-pro; ignored by kling-v1.6-standard so
    // the call still succeeds if someone overrides REPLICATE_VIDEO_MODEL
    // back to standard for cost reasons.
    end_image: input.afterImageUrl,
    duration: pickDuration(input.kwp),
    aspect_ratio: '1:1',
    // cfg_scale 0..1 = how strictly the model adheres to the
    // prompt + image conditioning. 1.0 = max adherence — we want it
    // since we're providing a precise end_image (no creative freedom
    // needed). 0.5 was the Standard-era default and produced the
    // watery transitions because the model wandered.
    cfg_scale: 1.0,
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
