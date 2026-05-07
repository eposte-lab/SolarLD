/**
 * Smoke test 01 — Login page render
 *
 * Verifies that the /login page:
 *   - Loads with HTTP 200 (or a client-navigated equivalent)
 *   - Renders the email + password inputs
 *   - Renders the submit button
 *   - Shows the SolarLead brand mark
 *
 * No auth required. Works against any running server.
 */

import { test, expect } from '@playwright/test';

test.describe('Login page', () => {
  test('renders the login form', async ({ page }) => {
    await page.goto('/login');

    // Brand mark
    await expect(
      page.getByText('SolarLead', { exact: true }).first(),
    ).toBeVisible();

    // Email input
    await expect(
      page.locator('input[type="email"]'),
    ).toBeVisible();

    // Password input
    await expect(
      page.locator('input[type="password"]'),
    ).toBeVisible();

    // Submit button
    await expect(
      page.getByRole('button', { name: /accedi/i }),
    ).toBeVisible();
  });

  test('shows error message on failed login', async ({ page }) => {
    await page.goto('/login');

    // Mock Supabase auth to return 400 Invalid credentials
    await page.route('**/auth/v1/token**', (route) => {
      route.fulfill({
        status: 400,
        contentType: 'application/json',
        body: JSON.stringify({
          error: 'invalid_grant',
          error_description: 'Invalid login credentials',
        }),
      });
    });

    await page.locator('input[type="email"]').fill('wrong@example.com');
    await page.locator('input[type="password"]').fill('wrongpassword');
    await page.getByRole('button', { name: /accedi/i }).click();

    // The error container should appear
    await expect(
      page.locator('text=Invalid login credentials'),
    ).toBeVisible({ timeout: 5_000 });
  });
});
