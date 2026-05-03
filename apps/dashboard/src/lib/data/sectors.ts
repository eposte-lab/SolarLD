/**
 * Sector palette data layer — Sprint C.2.
 *
 * Wraps `GET /v1/sectors/wizard-groups`. The catalog is stable
 * reference data (changes only via DB migration) so callers can
 * cache the result for the page lifetime.
 */

import { apiFetch } from '../api-client';
import type { WizardGroupOption } from '../../types/modules';

/**
 * Fetch the curated list of `wizard_group` palettes available for
 * tenant onboarding. Order is stable (curated groups first, then
 * alphabetical tail) so the UI checkbox order is deterministic.
 */
export async function listWizardGroups(): Promise<WizardGroupOption[]> {
  return apiFetch<WizardGroupOption[]>('/v1/sectors/wizard-groups');
}
