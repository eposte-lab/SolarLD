/**
 * Playwright configuration for dashboard smoke tests.
 *
 * Tests are split into two groups:
 *
 *   public  — tests that work against any server with no auth.
 *             Always run in CI via `pnpm test:e2e`.
 *
 *   auth    — tests that need a live tenant session.
 *             Skipped unless E2E_TEST_EMAIL + E2E_TEST_PASSWORD are set.
 *             Run locally or in a dedicated staging CI job with secrets.
 *
 * The `webServer` block starts `next dev` automatically if BASE_URL is not
 * overridden, so local runs work out of the box.
 *
 * Environment variables:
 *   BASE_URL            — target URL (default: http://localhost:3000)
 *   E2E_TEST_EMAIL      — test user email (triggers authenticated tests)
 *   E2E_TEST_PASSWORD   — test user password
 *   NEXT_PUBLIC_SUPABASE_URL      — required for next dev to boot
 *   NEXT_PUBLIC_SUPABASE_ANON_KEY — required for next dev to boot
 */

import { defineConfig, devices } from '@playwright/test';

const BASE_URL = process.env.BASE_URL ?? 'http://localhost:3000';

export default defineConfig({
  testDir: './tests',
  /* Use the compiled Next.js app — start dev server if BASE_URL is localhost */
  webServer: BASE_URL.startsWith('http://localhost')
    ? {
        command: 'pnpm dev',
        url: BASE_URL,
        reuseExistingServer: true,
        timeout: 120_000,
        env: {
          NEXT_PUBLIC_SUPABASE_URL:
            process.env.NEXT_PUBLIC_SUPABASE_URL ?? 'https://placeholder.supabase.co',
          NEXT_PUBLIC_SUPABASE_ANON_KEY:
            process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? 'ci-placeholder',
          NEXT_PUBLIC_API_URL:
            process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000',
        },
      }
    : undefined,

  use: {
    baseURL: BASE_URL,
    /* Collect trace on first retry */
    trace: 'on-first-retry',
    /* Sensible timeout for SSR pages */
    actionTimeout: 10_000,
    navigationTimeout: 30_000,
  },

  /* Fail fast in CI, slower locally */
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,

  /* Reporter: compact in CI, list locally */
  reporter: process.env.CI ? 'github' : 'list',

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  /* Capture screenshots + video on failure */
  screenshot: 'only-on-failure',
  video: 'retain-on-failure',

  /* Output directory for test artifacts */
  outputDir: 'playwright-results',
});
