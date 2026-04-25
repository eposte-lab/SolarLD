'use server';

/**
 * Server actions for the campaigns hub.
 *
 * createCampaign — POST to FastAPI `/v1/acquisition-campaigns` with
 *   the five module configs copied from the tenant's current modules
 *   (if the user doesn't override them in the form), then redirect to
 *   the new campaign's detail page.
 *
 * updateCampaignStatus — shortcut for activate / pause / archive.
 */

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';

import { getCurrentTenantContext } from '@/lib/data/tenant';
import { getModulesForTenant } from '@/lib/data/modules.server';
import { createSupabaseServerClient } from '@/lib/supabase/server';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

// ---------------------------------------------------------------------------
// createCampaign
// ---------------------------------------------------------------------------

export async function createCampaign(formData: FormData): Promise<void> {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const name = String(formData.get('name') ?? '').trim();
  const description = String(formData.get('description') ?? '').trim();

  if (!name) redirect('/campaigns/new?error=missing_name');

  // Start from the tenant's current module configs as sensible defaults.
  // The user can edit individual modules on the detail page afterwards.
  const modules = await getModulesForTenant(ctx.tenant.id);
  const byKey = Object.fromEntries(modules.map((m) => [m.module_key, m.config]));

  // Grab the user's access token from the SSR session.
  const sb = await createSupabaseServerClient();
  const {
    data: { session },
  } = await sb.auth.getSession();
  if (!session?.access_token) redirect('/login');

  let res: Response;
  try {
    res = await fetch(`${API_URL}/v1/acquisition-campaigns`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${session.access_token}`,
        'Content-Type': 'application/json',
      },
      cache: 'no-store',
      body: JSON.stringify({
        name,
        description: description || undefined,
        sorgente_config: byKey.sorgente ?? {},
        tecnico_config: byKey.tecnico ?? {},
        economico_config: byKey.economico ?? {},
        outreach_config: byKey.outreach ?? {},
        crm_config: byKey.crm ?? {},
      }),
    });
  } catch {
    redirect('/campaigns/new?error=api_unreachable');
  }

  if (!res.ok) {
    redirect('/campaigns/new?error=create_failed');
  }

  const campaign = (await res.json()) as { id: string };
  revalidatePath('/campaigns');
  redirect(`/campaigns/${campaign.id}`);
}

// ---------------------------------------------------------------------------
// updateCampaignStatus  (activate | pause | archive)
// ---------------------------------------------------------------------------

export async function updateCampaignStatus(formData: FormData): Promise<void> {
  const campaignId = String(formData.get('campaign_id') ?? '').trim();
  const action = String(formData.get('action') ?? '').trim() as
    | 'activate'
    | 'pause'
    | 'archive';

  if (!campaignId || !['activate', 'pause', 'archive'].includes(action)) {
    redirect('/campaigns?error=invalid_action');
  }

  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const sb = await createSupabaseServerClient();
  const {
    data: { session },
  } = await sb.auth.getSession();
  if (!session?.access_token) redirect('/login');

  const endpoint =
    action === 'archive'
      ? `/v1/acquisition-campaigns/${campaignId}`
      : `/v1/acquisition-campaigns/${campaignId}/${action}`;

  const method = action === 'archive' ? 'DELETE' : 'POST';

  let res: Response;
  try {
    res = await fetch(`${API_URL}${endpoint}`, {
      method,
      headers: {
        Authorization: `Bearer ${session.access_token}`,
        'Content-Type': 'application/json',
      },
      cache: 'no-store',
    });
  } catch {
    redirect('/campaigns?error=api_unreachable');
  }

  if (!res.ok) {
    redirect(`/campaigns?error=status_change_failed`);
  }

  revalidatePath('/campaigns');
  revalidatePath(`/campaigns/${campaignId}`);
  redirect(`/campaigns/${campaignId}`);
}
