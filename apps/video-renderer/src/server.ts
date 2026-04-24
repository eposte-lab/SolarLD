/**
 * Express sidecar — receives render requests from the FastAPI Creative
 * Agent, produces a 6s MP4 + GIF pair via Remotion, uploads both to
 * Supabase Storage, and returns public URLs.
 *
 *   POST /render          { ...solarTransitionSchema, outputPath, bucket? }
 *   GET  /health          { status, service, version }
 *
 * Handlers are factored out of the Express bootstrap so vitest can
 * exercise them without spinning a real HTTP server.
 */
import express, { type Request, type Response, type NextFunction } from 'express';
import type { SupabaseClient } from '@supabase/supabase-js';

import {
  type RenderRequest,
  type RenderResult,
  buildSupabaseClient,
  renderRequestSchema,
  renderTransition,
  warmupBrowser,
} from './render';

// ---------------------------------------------------------------------------
// Handlers (pure — accept a `supabase` dep so tests can inject a fake)
// ---------------------------------------------------------------------------

export const healthHandler = (_req: Request, res: Response): void => {
  res.json({
    status: 'ok',
    service: 'video-renderer',
    version: process.env.npm_package_version ?? '0.1.0',
  });
};

export interface RenderHandlerDeps {
  supabase: SupabaseClient;
  /** Override the render function for tests. */
  render?: (req: RenderRequest) => Promise<RenderResult>;
}

export const buildRenderHandler =
  (deps: RenderHandlerDeps) =>
  async (req: Request, res: Response, next: NextFunction): Promise<void> => {
    const parsed = renderRequestSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: parsed.error.flatten() });
      return;
    }
    try {
      const runner =
        deps.render ?? ((r: RenderRequest) => renderTransition(r, { supabase: deps.supabase }));
      const result = await runner(parsed.data);
      res.json(result);
    } catch (err) {
      next(err);
    }
  };

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

export const buildApp = (deps: RenderHandlerDeps): express.Express => {
  const app = express();
  app.use(express.json({ limit: '2mb' }));
  app.get('/health', healthHandler);
  app.post('/render', buildRenderHandler(deps));
  app.use(
    (err: Error, _req: Request, res: Response, _next: NextFunction): void => {
      // eslint-disable-next-line no-console
      console.error('[video-renderer] render failed:', err);
      res.status(500).json({ error: err.message });
    },
  );
  return app;
};

// Start only when executed directly (not when imported by tests)
if (require.main === module) {
  const PORT = Number(process.env.PORT ?? 4000);
  const app = buildApp({ supabase: buildSupabaseClient() });

  const server = app.listen(PORT, () => {
    // eslint-disable-next-line no-console
    console.log(`[video-renderer] listening on :${PORT}`);
  });

  // Warm up Remotion's browser right after binding so the first /render
  // request doesn't pay the cold-start penalty (Chrome launch + bundle).
  warmupBrowser().catch((err) => {
    // eslint-disable-next-line no-console
    console.warn('[video-renderer] browser warmup failed (non-fatal):', err);
  });

  // Graceful shutdown
  const shutdown = () => {
    // eslint-disable-next-line no-console
    console.log('[video-renderer] shutting down…');
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(1), 10_000);
  };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
}
