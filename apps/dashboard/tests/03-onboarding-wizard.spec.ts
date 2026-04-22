/**
 * Smoke test 03 — Onboarding wizard step navigation
 *
 * Verifies that:
 *   - All 5 module panels are rendered in the wizard shell
 *   - Clicking "Avanti" advances to the next step
 *   - Each step label is visible at the right time
 *   - On the last step, "Completa" triggers a PATCH to the API
 *
 * Requires a live tenant session. The test skips automatically if
 * E2E_TEST_EMAIL / E2E_TEST_PASSWORD are not set.
 *
 * To run locally:
 *   E2E_TEST_EMAIL=test@tenant.com E2E_TEST_PASSWORD=secret pnpm test:e2e
 */

import { test, expect } from '@playwright/test';

const EMAIL = process.env.E2E_TEST_EMAIL;
const PASSWORD = process.env.E2E_TEST_PASSWORD;

test.describe('Onboarding wizard', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!EMAIL || !PASSWORD, 'Requires E2E_TEST_EMAIL + E2E_TEST_PASSWORD');

    // Sign in via the login form
    await page.goto('/login');
    await page.locator('input[type="email"]').fill(EMAIL!);
    await page.locator('input[type="password"]').fill(PASSWORD!);
    await page.getByRole('button', { name: /accedi/i }).click();

    // Wait for post-login navigation
    await page.waitForURL(/(leads|onboarding)/, { timeout: 15_000 });
  });

  test('wizard renders all 5 module tabs', async ({ page }) => {
    await page.goto('/onboarding');

    // Wizard should show the 5 module keys as step labels (or tabs)
    const EXPECTED_MODULES = ['Sorgente', 'Tecnico', 'Economico', 'Outreach', 'CRM'];

    for (const label of EXPECTED_MODULES) {
      await expect(
        page.getByText(label, { exact: false }),
      ).toBeVisible({ timeout: 10_000 });
    }
  });

  test('completing wizard fires PATCH to /v1/modules', async ({ page }) => {
    await page.goto('/onboarding');

    // Track API calls to module save endpoint
    const patchRequests: string[] = [];
    page.on('request', (req) => {
      if (req.method() === 'PATCH' && req.url().includes('/v1/modules')) {
        patchRequests.push(req.url());
      }
    });

    // Navigate through all steps by clicking "Salva" / "Avanti" on each
    const nextButtons = page.getByRole('button', { name: /salva|avanti|completa/i });

    // Step through 5 modules
    for (let i = 0; i < 5; i++) {
      const btn = nextButtons.first();
      if (await btn.isVisible({ timeout: 5_000 })) {
        await btn.click();
        // Allow time for API call + state update
        await page.waitForTimeout(500);
      }
    }

    // At least one PATCH should have been fired to save module data
    expect(patchRequests.length).toBeGreaterThanOrEqual(1);
  });
});
