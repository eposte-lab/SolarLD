/**
 * Smoke test 02 — Auth guard redirect
 *
 * Verifies that all protected routes redirect an unauthenticated visitor to
 * /login. This exercises the Next.js middleware (`src/middleware.ts`) +
 * the Supabase SSR session check.
 *
 * We mock the Supabase auth/getUser call to return a "not authenticated"
 * response so the middleware redirects. The redirect itself is a browser-
 * level HTTP 302 response that Playwright follows automatically; we assert
 * on the final URL.
 *
 * No real Supabase credentials required.
 */

import { test, expect } from '@playwright/test';

/**
 * Mock the Supabase auth endpoint so the middleware sees "no user".
 * The SSR middleware calls /auth/v1/user with the session token.
 * Returning 401 / null user triggers the redirect guard.
 */
async function mockUnauthenticated(page: import('@playwright/test').Page) {
  await page.route('**/auth/v1/user', (route) => {
    route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ message: 'JWT expired' }),
    });
  });
}

const PROTECTED_ROUTES = [
  '/leads',
  '/campaigns',
  '/territories',
  '/settings',
  '/settings/modules',
  '/analytics',
];

test.describe('Auth guard redirects', () => {
  for (const route of PROTECTED_ROUTES) {
    test(`redirects ${route} → /login`, async ({ page }) => {
      await mockUnauthenticated(page);
      await page.goto(route);

      // Wait for navigation to settle
      await page.waitForURL('**/login', { timeout: 10_000 });
      expect(page.url()).toContain('/login');
    });
  }

  test('/login is reachable without auth', async ({ page }) => {
    await mockUnauthenticated(page);
    await page.goto('/login');

    // Should stay on /login, NOT get redirected away
    await expect(page.locator('input[type="email"]')).toBeVisible();
    expect(page.url()).toContain('/login');
  });
});
