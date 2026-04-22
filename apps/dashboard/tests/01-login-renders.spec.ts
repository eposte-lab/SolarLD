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
      page.getByText('SolarLead'),
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

  test('submit button is disabled while loading', async ({ page }) => {
    await page.goto('/login');

    // Intercept the Supabase signIn API call to hang indefinitely
    // so we can observe the loading state.
    await page.route('**/auth/v1/token**', async (route) => {
      // Never fulfill — the loading spinner should appear
      // We abort after a short delay to not stall the test
      await new Promise((resolve) => setTimeout(resolve, 200));
      await route.abort();
    });

    const emailInput = page.locator('input[type="email"]');
    const passwordInput = page.locator('input[type="password"]');
    const submitBtn = page.getByRole('button', { name: /accedi/i });

    await emailInput.fill('test@example.com');
    await passwordInput.fill('password123');

    await submitBtn.click();

    // During the inflight request the button becomes disabled
    await expect(submitBtn).toBeDisabled();
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
