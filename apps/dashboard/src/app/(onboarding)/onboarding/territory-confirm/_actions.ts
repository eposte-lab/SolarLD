/**
 * Server action for the territory-confirm onboarding step.
 *
 * Hits POST /v1/onboarding/territory-confirm on the FastAPI backend,
 * which sets `tenants.territory_locked_at = now()`. Relies on the
 * Supabase SSR session for the Bearer token. Idempotent — a second
 * call with an already-locked tenant is a no-op and returns success.
 */

'use server';

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';

import { createSupabaseServerClient } from '@/lib/supabase/server';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export async function confirmTerritory(): Promise<void> {
  const sb = await createSupabaseServerClient();
  const {
    data: { session },
  } = await sb.auth.getSession();
  if (!session?.access_token) {
    redirect('/login');
  }

  let res: Response;
  try {
    res = await fetch(`${API_URL}/v1/onboarding/territory-confirm`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${session.access_token}`,
        'Content-Type': 'application/json',
      },
      cache: 'no-store',
    });
  } catch {
    redirect('/onboarding/territory-confirm?error=api_unreachable');
  }

  if (!res.ok) {
    redirect(`/onboarding/territory-confirm?error=confirm_failed_${res.status}`);
  }

  // The dashboard layout picks up the fresh `territory_locked_at`
  // and stops redirecting us back to /onboarding.
  revalidatePath('/', 'layout');
  redirect('/?welcome=1');
}
