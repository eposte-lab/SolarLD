'use client';

/**
 * Client helper that serializes the wizard state into the shape the
 * FastAPI route expects (`WizardIn` in `apps/api/src/routes/tenant_config.py`)
 * and handles the POST.
 *
 * The wire format is deliberately flat — the Python side builds the
 * nested `technical_filters` object itself, so the form sends
 * `min_kwp_b2b` / `min_kwp_b2c` as top-level fields.
 *
 * On success the server returns the full `TenantConfigOut`; we don't
 * use it on the client (we router.refresh() and the layout re-reads
 * from Supabase), but returning the payload keeps the hook composable.
 */

import { api, ApiError } from '@/lib/api-client';

import type { WizardForm } from './wizard-types';

export interface WizardSubmitResult {
  ok: true;
}

export interface WizardSubmitError {
  ok: false;
  message: string;
  status?: number;
}

export async function submitWizard(
  form: WizardForm,
): Promise<WizardSubmitResult | WizardSubmitError> {
  try {
    await api.post<unknown>('/v1/tenant-config', form);
    return { ok: true };
  } catch (err) {
    if (err instanceof ApiError) {
      // FastAPI 422 → body is `{ detail: [{ loc, msg, type }, ...] }`.
      // Surface the first message; the form should have prevented
      // most of these via client-side `canAdvance`.
      let message = err.message;
      const body = err.body as
        | { detail?: Array<{ msg?: string }> | string }
        | undefined;
      if (body && typeof body === 'object' && Array.isArray(body.detail)) {
        const first = body.detail[0];
        if (first?.msg) message = first.msg;
      } else if (body && typeof body === 'object' && typeof body.detail === 'string') {
        message = body.detail;
      }
      return { ok: false, message, status: err.status };
    }
    return {
      ok: false,
      message: err instanceof Error ? err.message : 'Errore sconosciuto',
    };
  }
}
