/**
 * Shared zod schemas for the video-renderer sidecar.
 *
 * Kept separate from render.ts so the schemas can be imported by the
 * unit tests without pulling in the Replicate / ffmpeg / Supabase
 * runtime dependencies.
 *
 * Schema kept backward-compatible with the Remotion-era request shape so
 * the Python `remotion_service.py` client doesn't need to break — we
 * simply accept (and silently ignore) `scene3d` when it's still sent.
 */
import { z } from 'zod';

/**
 * Input properties describing the before→after solar transition we
 * want generated. Same field names the Python client already sends.
 */
export const transitionInputSchema = z.object({
  beforeImageUrl: z.string().url(),
  afterImageUrl: z.string().url(),
  kwp: z.number(),
  yearlySavingsEur: z.number(),
  paybackYears: z.number(),
  co2TonnesLifetime: z.number().optional(),
  tenantName: z.string(),
  brandPrimaryColor: z
    .string()
    .regex(/^#[0-9a-fA-F]{3,8}$/)
    .default('#0F766E'),
  brandLogoUrl: z.string().url().optional(),
  /**
   * Legacy 3-D scene payload from the Three.js era. Accepted but
   * ignored — kept here purely so older clients that still serialize
   * it don't get a 400. Will be removed once `apps/api` is rebuilt.
   */
  scene3d: z.unknown().optional(),
});
export type TransitionInput = z.infer<typeof transitionInputSchema>;

/**
 * Full HTTP request body for POST /render — composition props + the
 * Supabase storage destination.
 */
export const renderRequestSchema = transitionInputSchema.extend({
  outputPath: z
    .string()
    .min(1)
    .refine((p) => !p.startsWith('/') && !p.includes('..'), {
      message: 'outputPath must be a relative, non-traversing path',
    }),
  bucket: z.string().min(1).default('renderings'),
});
export type RenderRequest = z.infer<typeof renderRequestSchema>;

/**
 * Shape of the JSON returned to the Python CreativeAgent. Same fields
 * the Remotion sidecar used to return, so the caller doesn't change.
 */
export interface RenderResult {
  mp4Url: string;
  gifUrl: string;
  durationMs: number;
}

// Back-compat alias — some older imports may still reach for the
// Three.js-era name. Point them at the trimmed schema.
export const solarTransitionSchema = transitionInputSchema;
