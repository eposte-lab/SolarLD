'use server';

/**
 * Server actions for the device-authorization admin page.
 *
 * All actions are tenant-scoped: a device row can only be touched
 * if its tenant_id matches the current user's tenant. We use the
 * service-role client (which has no RLS) and add the tenant filter
 * explicitly on every query — same pattern as the gate itself.
 */

import { createClient } from '@supabase/supabase-js';
import { revalidatePath } from 'next/cache';

import { getCurrentTenantContext } from '@/lib/data/tenant';

function getServiceClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.SUPABASE_SERVICE_ROLE_KEY!,
    { auth: { persistSession: false, autoRefreshToken: false } },
  );
}

async function assertTenant(): Promise<{ tenantId: string }> {
  const ctx = await getCurrentTenantContext();
  if (!ctx) throw new Error('Not authenticated');
  return { tenantId: ctx.tenant.id };
}

// ---------------------------------------------------------------------------
// Revoke a device — frees its slot. The device row is kept (audit trail)
// but `revoked_at` is set so it stops matching the gate.
// ---------------------------------------------------------------------------
export async function revokeDevice(formData: FormData): Promise<void> {
  const { tenantId } = await assertTenant();
  const id = String(formData.get('device_id') ?? '');
  if (!id) throw new Error('Missing device_id');

  const sb = getServiceClient();
  await sb
    .from('tenant_authorized_devices')
    .update({ revoked_at: new Date().toISOString() })
    .eq('id', id)
    .eq('tenant_id', tenantId);

  revalidatePath('/settings/devices');
}

// ---------------------------------------------------------------------------
// Promote a device to admin role — admin devices don't free up when the
// quota is recomputed (well, they still count toward max_total but they
// are pinned and not auto-revokable). Useful for the operator's machine.
// ---------------------------------------------------------------------------
export async function promoteDeviceToAdmin(formData: FormData): Promise<void> {
  const { tenantId } = await assertTenant();
  const id = String(formData.get('device_id') ?? '');
  if (!id) throw new Error('Missing device_id');

  const sb = getServiceClient();
  await sb
    .from('tenant_authorized_devices')
    .update({ role: 'admin' })
    .eq('id', id)
    .eq('tenant_id', tenantId);

  revalidatePath('/settings/devices');
}

// ---------------------------------------------------------------------------
// Demote admin → client (unpins the device).
// ---------------------------------------------------------------------------
export async function demoteDeviceToClient(formData: FormData): Promise<void> {
  const { tenantId } = await assertTenant();
  const id = String(formData.get('device_id') ?? '');
  if (!id) throw new Error('Missing device_id');

  const sb = getServiceClient();
  await sb
    .from('tenant_authorized_devices')
    .update({ role: 'client' })
    .eq('id', id)
    .eq('tenant_id', tenantId);

  revalidatePath('/settings/devices');
}

// ---------------------------------------------------------------------------
// Rename a device.
// ---------------------------------------------------------------------------
export async function renameDevice(formData: FormData): Promise<void> {
  const { tenantId } = await assertTenant();
  const id = String(formData.get('device_id') ?? '');
  const newName = String(formData.get('display_name') ?? '').trim().slice(0, 80);
  if (!id || !newName) return;

  const sb = getServiceClient();
  await sb
    .from('tenant_authorized_devices')
    .update({ display_name: newName })
    .eq('id', id)
    .eq('tenant_id', tenantId);

  revalidatePath('/settings/devices');
}

// ---------------------------------------------------------------------------
// Toggle the master device-gate flag for the tenant. Used from the
// /settings landing too via this same action.
// ---------------------------------------------------------------------------
export async function setDeviceGateEnabled(formData: FormData): Promise<void> {
  const { tenantId } = await assertTenant();
  const enabled = formData.get('enabled') === 'true';
  const maxTotal = Number(formData.get('max_total') ?? 3);
  const idleMin = Number(formData.get('idle_minutes') ?? 30);

  const update: Record<string, unknown> = {
    demo_device_limit_enabled: enabled,
  };
  if (Number.isFinite(maxTotal) && maxTotal >= 1 && maxTotal <= 20) {
    update.demo_device_max_total = maxTotal;
  }
  if (Number.isFinite(idleMin) && idleMin >= 5 && idleMin <= 1440) {
    update.demo_device_idle_timeout_minutes = idleMin;
  }

  const sb = getServiceClient();
  await sb.from('tenants').update(update).eq('id', tenantId);

  revalidatePath('/settings/devices');
  revalidatePath('/settings');
}
