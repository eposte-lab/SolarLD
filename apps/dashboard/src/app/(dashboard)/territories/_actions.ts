/**
 * Server actions for the territories page.
 *
 * Three operations:
 *   - `createTerritory`  — insert a new territory row (with optional bbox)
 *   - `deleteTerritory`  — delete by id (RLS enforces tenant ownership)
 *   - `triggerScan`      — POST to the FastAPI backend to enqueue a Hunter
 *                          scan; the backend validates the JWT and enqueues
 *                          a `hunter_task` job on the arq worker.
 *
 * All mutations `revalidatePath('/territories')` so the SSR list refreshes.
 */

'use server';

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';

import { createSupabaseServerClient } from '@/lib/supabase/server';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { getTierBudget } from '@/lib/data/tier';
import { getScanUsageMtdCents } from '@/lib/data/usage';
import type { TerritoryBbox, TerritoryType } from '@/types/db';

const VALID_TYPES: readonly TerritoryType[] = [
  'cap',
  'comune',
  'provincia',
  'regione',
] as const;

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function assertType(raw: string | null): TerritoryType {
  if (raw && (VALID_TYPES as readonly string[]).includes(raw)) {
    return raw as TerritoryType;
  }
  throw new Error(`territory_type invalid: ${raw ?? '(empty)'}`);
}

function clampPriority(raw: string | null): number {
  const n = Number(raw ?? 5);
  if (!Number.isFinite(n)) return 5;
  return Math.min(10, Math.max(1, Math.round(n)));
}

/**
 * Parse the four bbox inputs from the form.
 * Returns null if any of the four fields is absent or non-numeric.
 * Returns the bbox object if all four are valid lat/lng values.
 */
function parseBbox(fd: FormData): TerritoryBbox | null {
  const neLat = Number(fd.get('bbox_ne_lat'));
  const neLng = Number(fd.get('bbox_ne_lng'));
  const swLat = Number(fd.get('bbox_sw_lat'));
  const swLng = Number(fd.get('bbox_sw_lng'));

  if (
    !Number.isFinite(neLat) || !Number.isFinite(neLng) ||
    !Number.isFinite(swLat) || !Number.isFinite(swLng) ||
    (neLat === 0 && neLng === 0 && swLat === 0 && swLng === 0)
  ) {
    return null;
  }
  // Basic sanity: NE must be north-east of SW.
  if (neLat <= swLat || neLng <= swLng) return null;
  return { ne: { lat: neLat, lng: neLng }, sw: { lat: swLat, lng: swLng } };
}

// ---------------------------------------------------------------------------
// createTerritory
// ---------------------------------------------------------------------------

export async function createTerritory(formData: FormData): Promise<void> {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const type = assertType(formData.get('type') as string | null);
  const code = String(formData.get('code') ?? '').trim();
  const name = String(formData.get('name') ?? '').trim();
  const priority = clampPriority(formData.get('priority') as string | null);
  const excluded = formData.get('excluded') === 'on';
  const bbox = parseBbox(formData); // nullable — scanning requires it

  if (!code) redirect('/territories?error=missing_code');
  if (!name) redirect('/territories?error=missing_name');

  // CAP must be exactly 5 digits in Italy — cheap guard.
  if (type === 'cap' && !/^\d{5}$/.test(code)) {
    redirect('/territories?error=invalid_cap');
  }

  const sb = await createSupabaseServerClient();
  const row: Record<string, unknown> = {
    tenant_id: ctx.tenant.id,
    type,
    code,
    name,
    priority,
    excluded,
  };
  if (bbox) row.bbox = bbox;

  const { error } = await sb.from('territories').insert(row);
  if (error) {
    if (error.code === '23505') redirect('/territories?error=duplicate');
    redirect(`/territories?error=${encodeURIComponent(error.message).slice(0, 120)}`);
  }

  revalidatePath('/territories');
  redirect(`/territories?created=${encodeURIComponent(code)}`);
}

// ---------------------------------------------------------------------------
// deleteTerritory
// ---------------------------------------------------------------------------

export async function deleteTerritory(formData: FormData): Promise<void> {
  const id = String(formData.get('id') ?? '').trim();
  if (!id) redirect('/territories?error=missing_id');

  const sb = await createSupabaseServerClient();
  const { error } = await sb.from('territories').delete().eq('id', id);
  if (error) {
    redirect(`/territories?error=${encodeURIComponent(error.message).slice(0, 120)}`);
  }
  revalidatePath('/territories');
  redirect('/territories?deleted=1');
}

// ---------------------------------------------------------------------------
// triggerScan
// ---------------------------------------------------------------------------

/**
 * Enqueue a Hunter Agent scan via the FastAPI backend.
 *
 * The backend validates the JWT (same Supabase project), checks the
 * territory belongs to the caller's tenant, then pushes a `hunter_task`
 * job onto the arq/Redis queue. The arq worker picks it up and runs
 * HunterAgent in the background.
 *
 * Flash params on redirect:
 *   ?scanning=<territory_id>  — scan enqueued (arq job_id is opaque)
 *   ?error=no_bbox            — territory saved without bbox — scan blocked
 *   ?error=scan_failed        — API returned non-2xx
 *   ?error=scan_no_auth       — session missing or expired
 *   ?error=budget_exceeded    — tenant has consumed its MTD scan budget
 */
export async function triggerScan(formData: FormData): Promise<void> {
  const id = String(formData.get('id') ?? '').trim();
  const hasBbox = formData.get('has_bbox') === '1';

  if (!hasBbox) {
    redirect('/territories?error=no_bbox');
  }

  // Tier budget pre-check — avoid firing a job we know will be rejected
  // downstream. This is only informational; the authoritative enforcement
  // lives in the Python agents (service-role visibility across tenants).
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');
  const scanBudget = getTierBudget(ctx.tenant, 'monthly_scan_budget_cents');
  if (scanBudget !== null) {
    const used = await getScanUsageMtdCents(ctx.tenant.id);
    if (used >= scanBudget) {
      redirect('/territories?error=budget_exceeded');
    }
  }

  // Grab the user's access token from the SSR session.
  const sb = await createSupabaseServerClient();
  const {
    data: { session },
  } = await sb.auth.getSession();
  if (!session?.access_token) {
    redirect('/login');
  }

  const maxRoofs = 500; // safe default — operator can re-trigger for more
  const res = await fetch(
    `${API_URL}/v1/territories/${id}/scan?max_roofs=${maxRoofs}`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${session.access_token}`,
        'Content-Type': 'application/json',
      },
      // next: { revalidate: 0 } — this is a mutation, never cached.
      cache: 'no-store',
    },
  );

  if (!res.ok) {
    let detail = 'scan_failed';
    try {
      const body = await res.json() as { detail?: string };
      if (body.detail?.includes('no bbox')) detail = 'no_bbox';
    } catch {
      // ignore parse error
    }
    redirect(`/territories?error=${detail}`);
  }

  revalidatePath('/territories');
  redirect(`/territories?scanning=${encodeURIComponent(id)}`);
}
