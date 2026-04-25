import { describe, it, expect } from 'vitest';
import { solarTransitionSchema, renderRequestSchema } from '../schema';
import { stripNonSchemaProps, joinPath } from '../render';

/**
 * Pure tests for the zod schemas and the helpers that transform
 * incoming /render bodies into the video-generation input. No
 * Replicate calls, no Supabase — just shape assertions.
 */

describe('solarTransitionSchema', () => {
  const baseProps = {
    beforeImageUrl: 'https://example.com/b.png',
    afterImageUrl: 'https://example.com/a.png',
    kwp: 10,
    yearlySavingsEur: 1500,
    paybackYears: 7,
    tenantName: 'Solare Rapido SRL',
  };

  it('accepts a minimal valid payload and fills defaults', () => {
    const parsed = solarTransitionSchema.parse(baseProps);
    expect(parsed.brandPrimaryColor).toBe('#0F766E'); // default
    expect(parsed.brandLogoUrl).toBeUndefined();
    expect(parsed.co2TonnesLifetime).toBeUndefined();
  });

  it('rejects non-URL image inputs', () => {
    const bad = solarTransitionSchema.safeParse({
      ...baseProps,
      beforeImageUrl: 'not-a-url',
    });
    expect(bad.success).toBe(false);
  });

  it('rejects a non-hex brand primary color', () => {
    const bad = solarTransitionSchema.safeParse({
      ...baseProps,
      brandPrimaryColor: 'teal',
    });
    expect(bad.success).toBe(false);
  });

  it('accepts 3-, 6-, and 8-digit hex colors', () => {
    for (const hex of ['#abc', '#aabbcc', '#aabbccdd']) {
      const ok = solarTransitionSchema.safeParse({ ...baseProps, brandPrimaryColor: hex });
      expect(ok.success).toBe(true);
    }
  });

  it('keeps optional logo URL when provided', () => {
    const parsed = solarTransitionSchema.parse({
      ...baseProps,
      brandLogoUrl: 'https://cdn/logo.png',
    });
    expect(parsed.brandLogoUrl).toBe('https://cdn/logo.png');
  });
});

describe('renderRequestSchema', () => {
  const base = {
    beforeImageUrl: 'https://example.com/b.png',
    afterImageUrl: 'https://example.com/a.png',
    kwp: 10,
    yearlySavingsEur: 1500,
    paybackYears: 7,
    tenantName: 'ACME',
    outputPath: 'tenant-abc/lead-123',
  };

  it('defaults bucket to "renderings"', () => {
    const parsed = renderRequestSchema.parse(base);
    expect(parsed.bucket).toBe('renderings');
  });

  it('rejects absolute outputPaths', () => {
    const bad = renderRequestSchema.safeParse({ ...base, outputPath: '/evil' });
    expect(bad.success).toBe(false);
  });

  it('rejects path traversal attempts', () => {
    const bad = renderRequestSchema.safeParse({
      ...base,
      outputPath: 'tenant/../../../etc/passwd',
    });
    expect(bad.success).toBe(false);
  });

  it('rejects empty outputPath', () => {
    const bad = renderRequestSchema.safeParse({ ...base, outputPath: '' });
    expect(bad.success).toBe(false);
  });
});

describe('stripNonSchemaProps', () => {
  it('removes outputPath and bucket before passing to Remotion', () => {
    const stripped = stripNonSchemaProps({
      beforeImageUrl: 'https://example.com/b.png',
      afterImageUrl: 'https://example.com/a.png',
      kwp: 10,
      yearlySavingsEur: 1500,
      paybackYears: 7,
      tenantName: 'ACME',
      brandPrimaryColor: '#0F766E',
      outputPath: 'tenant-abc/lead-123',
      bucket: 'renderings',
    });
    expect(stripped).not.toHaveProperty('outputPath');
    expect(stripped).not.toHaveProperty('bucket');
    expect(stripped.beforeImageUrl).toBe('https://example.com/b.png');
  });
});

describe('joinPath', () => {
  it('joins segments without leading/trailing slashes', () => {
    expect(joinPath('renderings', 'tenant-abc', 'lead-123', 'transition.mp4')).toBe(
      'renderings/tenant-abc/lead-123/transition.mp4',
    );
  });

  it('strips redundant slashes', () => {
    expect(joinPath('/a/', '/b/', '/c/')).toBe('a/b/c');
  });

  it('skips empty segments', () => {
    expect(joinPath('a', '', 'b')).toBe('a/b');
  });
});
