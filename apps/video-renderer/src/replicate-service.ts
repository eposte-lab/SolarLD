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
 * Animation style (May 2026 — tenant feedback): "i pannelli non devono
 * cadere dal cielo, ma comparire gradualmente sul tetto dal tetto
 * stesso, come se fossero già posizionati". Translation: panels are
 * already there — they're just initially invisible and they reveal
 * themselves on the roof surface via a smooth opacity fade-in. NO
 * physical entrance from any direction (sky, sides, behind, below).
 * The intended look is "an architectural rendering being progressively
 * unveiled by sunlight", NOT a construction/install animation.
 *
 * Iteration history:
 *   - "drop from above" → looked like Tetris falling
 *   - "slide in row by row" → conveyor-belt look, cheap-ad feel
 *   - "stop-motion pop-in" → still triggered occasional descent motion
 *   - **current**: "fade-in from the roof surface itself, as if pre-installed"
 *
 * Things deliberately NOT in the prompt:
 *   - panel geometry, count, position (covered by end_image)
 *   - text/logos (added in ffmpeg overlay post-processing where we
 *     control typography)
 */
const DEFAULT_PROMPT =
  'Photo-realistic aerial view of an Italian rooftop where solar panels are already installed but initially invisible, and they gradually reveal themselves on the roof surface itself via a smooth opacity fade-in — as if a soft sweep of sunlight is progressively unveiling panels that were always there. Each panel emerges IN PLACE on the roof: it starts fully transparent, then smoothly fades up to its final fully-opaque appearance at its exact final position, perfectly flush with the roof plane. The panels do NOT enter the scene from anywhere — not from above, not from the sky, not from the sides, not from below, not from off-screen. There is ZERO translation, ZERO descent, ZERO rotation, ZERO sliding, ZERO dropping. The reveal is a pure opacity transition happening on the roof surface itself, panel by panel, at a calm professional cadence (a few panels per second) in a non-linear order across the array, so the rooftop array fills in gradually until complete. Each panel sits flat against the roof tiles, with realistic contact shadows underneath consistent with already-installed hardware — the panels are part of the roof, never floating above it. CRITICAL BOUNDARY CONSTRAINT: panels appear ONLY within the exact rooftop footprint of the single target building — the same building shown in the before image. No panel may extend beyond the roofline edge, overflow onto adjacent rooftops, spill onto walls, ground, parking areas, driveways, lawns, or any surface outside the building perimeter. The panel array must remain fully contained inside the roof boundary at all times — even at the edges, even during reveal. Subtle ambient motion enriches the rest of the scene: soft cloud shadows drift gently across the ground, distant tree foliage rustles softly in the breeze, any visible cars on nearby streets drift forward smoothly. The static scene (ground, vegetation, neighbouring buildings, parked vehicles) remains stable. Fixed top-down camera, no zoom, no pan, no rotation, no tilt. Soft natural daylight with realistic shadows, photorealistic, sharp focus, calm and elegant professional architectural cinematography — the tone of a high-end real-estate render, not a construction timelapse.';

const NEGATIVE_PROMPT =
  'panels falling from sky, panels falling from above, panels descending from the air, panels dropping from above, panels arriving from above the roof, panels appearing in the air above the building, panels visible in the sky before landing, gravity drop animation, panels with motion trails, panels with vertical translation, panels with horizontal translation, panels sliding in from the sides, panels rotating into position, panels tilting before landing, panels hovering above the roof, panels floating above the roof, panels with a gap between panel and roof surface, panels disconnected from the roof plane, panels not flush with the roof, conveyor belt motion, rows of panels appearing simultaneously, rows being installed one at a time, edge-to-edge sweeping wipe, panels arriving from off-screen, panels carried by hands, panels carried by machines, construction workers, cranes, drones, scaffolding, installation animation, building under construction, water, liquid, pool, flood, wet surface, reflection of water, panels outside roof boundary, panels overflowing roofline, panels spilling beyond roof edge, panels on adjacent building, panels on neighbouring rooftop, panels on wall, panels on ground, panels on grass, panels on lawn, panels on pavement, panels on driveway, panels on parking lot, panels on cars, panels on trees, panels outside building footprint, panels on any surface other than the target roof, low quality, blurry, distorted geometry, warped roof, melting, morphing, dissolving, fade between two whole frames, cross-fade of full image, ghosting, double exposure, text, captions, logos, watermarks, cartoon, illustration, neon colors, weird lighting, camera motion, zoom, pan, rotation, dolly, tilt, jump cut, abrupt change';

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
