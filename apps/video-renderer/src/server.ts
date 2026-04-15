/**
 * Express sidecar — receives render requests from the FastAPI Creative Agent
 * and returns an mp4 + gif via Supabase Storage upload.
 *
 * POST /render
 *   body: { beforeImageUrl, afterImageUrl, kwp, yearlySavingsEur, paybackYears, tenantName, outputPath }
 *
 * Returns: { mp4Url, gifUrl, durationMs }
 */
import express, { type Request, type Response } from 'express';
import { z } from 'zod';

const app = express();
app.use(express.json({ limit: '2mb' }));

const RenderSchema = z.object({
  beforeImageUrl: z.string().url(),
  afterImageUrl: z.string().url(),
  kwp: z.number(),
  yearlySavingsEur: z.number(),
  paybackYears: z.number(),
  tenantName: z.string(),
  outputPath: z.string(),
});

app.get('/health', (_req: Request, res: Response) => {
  res.json({ status: 'ok', service: 'video-renderer' });
});

app.post('/render', async (req: Request, res: Response) => {
  const parsed = RenderSchema.safeParse(req.body);
  if (!parsed.success) {
    return res.status(400).json({ error: parsed.error.flatten() });
  }
  // TODO(Sprint 4-5):
  //  1) call @remotion/renderer renderMedia()
  //  2) upload mp4 + gif to Supabase Storage
  //  3) return public URLs
  return res.json({
    mp4Url: null,
    gifUrl: null,
    durationMs: 0,
    message: 'Render stub — implementation pending Sprint 4-5',
  });
});

const PORT = Number(process.env.PORT ?? 4000);
app.listen(PORT, () => {
  // eslint-disable-next-line no-console
  console.log(`[video-renderer] listening on :${PORT}`);
});
