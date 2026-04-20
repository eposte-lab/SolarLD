import { describe, it, expect, vi } from 'vitest';
import request from 'supertest';
import type { SupabaseClient } from '@supabase/supabase-js';
import { buildApp } from '../server';
import type { RenderRequest, RenderResult } from '../render';

/**
 * HTTP-level tests — we inject a fake `render()` so we never hit the
 * Remotion bundler or Supabase during the unit run.
 */

const fakeSupabase = {} as unknown as SupabaseClient;

const validBody = {
  beforeImageUrl: 'https://example.com/b.png',
  afterImageUrl: 'https://example.com/a.png',
  kwp: 10,
  yearlySavingsEur: 1500,
  paybackYears: 7,
  tenantName: 'ACME',
  outputPath: 'tenant-abc/lead-123',
};

describe('GET /health', () => {
  it('returns status ok + service name', async () => {
    const app = buildApp({
      supabase: fakeSupabase,
      render: vi.fn<(r: RenderRequest) => Promise<RenderResult>>(),
    });
    const resp = await request(app).get('/health');
    expect(resp.status).toBe(200);
    expect(resp.body.status).toBe('ok');
    expect(resp.body.service).toBe('video-renderer');
  });
});

describe('POST /render', () => {
  it('invokes the injected render() with the parsed body', async () => {
    const fakeResult: RenderResult = {
      mp4Url: 'https://cdn/transition.mp4',
      gifUrl: 'https://cdn/transition.gif',
      durationMs: 9876,
    };
    const renderFn = vi
      .fn<(r: RenderRequest) => Promise<RenderResult>>()
      .mockResolvedValue(fakeResult);
    const app = buildApp({ supabase: fakeSupabase, render: renderFn });

    const resp = await request(app).post('/render').send(validBody);

    expect(resp.status).toBe(200);
    expect(resp.body).toEqual(fakeResult);
    expect(renderFn).toHaveBeenCalledOnce();
    const firstArg = renderFn.mock.calls[0]?.[0];
    expect(firstArg?.beforeImageUrl).toBe(validBody.beforeImageUrl);
    expect(firstArg?.bucket).toBe('renderings'); // default filled by zod
  });

  it('returns 400 on invalid body (missing fields)', async () => {
    const app = buildApp({
      supabase: fakeSupabase,
      render: vi.fn<(r: RenderRequest) => Promise<RenderResult>>(),
    });
    const resp = await request(app).post('/render').send({ kwp: 10 });
    expect(resp.status).toBe(400);
    expect(resp.body.error).toBeDefined();
  });

  it('returns 400 when outputPath tries path traversal', async () => {
    const app = buildApp({
      supabase: fakeSupabase,
      render: vi.fn<(r: RenderRequest) => Promise<RenderResult>>(),
    });
    const resp = await request(app)
      .post('/render')
      .send({ ...validBody, outputPath: '../../etc/passwd' });
    expect(resp.status).toBe(400);
  });

  it('returns 500 when the render function throws', async () => {
    const app = buildApp({
      supabase: fakeSupabase,
      render: vi
        .fn<(r: RenderRequest) => Promise<RenderResult>>()
        .mockRejectedValue(new Error('ffmpeg exploded')),
    });
    const resp = await request(app).post('/render').send(validBody);
    expect(resp.status).toBe(500);
    expect(resp.body.error).toMatch(/ffmpeg exploded/);
  });
});
