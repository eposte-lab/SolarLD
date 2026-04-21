/**
 * `/settings/modules/[key]` — standalone edit page for one module.
 *
 * Reuses the same `ModulePanel` component the onboarding wizard uses,
 * so editing post-onboarding has the same form as the initial setup —
 * there's no drift between "first-time" and "edit" UX.
 *
 * The page redirects to `/settings/modules` on a 404 key rather than
 * surfacing a generic Not Found, keeping the installer in familiar
 * settings territory.
 */

import { notFound, redirect } from 'next/navigation';

import { ModulePanel } from '@/components/modules/ModulePanel';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { getModuleForTenant } from '@/lib/data/modules.server';
import type { ModuleKey } from '@/types/modules';

const VALID_KEYS: readonly ModuleKey[] = [
  'sorgente',
  'tecnico',
  'economico',
  'outreach',
  'crm',
] as const;

function isModuleKey(k: string): k is ModuleKey {
  return (VALID_KEYS as readonly string[]).includes(k);
}

export default async function ModuleEditPage({
  params,
}: {
  // Next.js 15 provides params as a Promise.
  params: Promise<{ key: string }>;
}) {
  const { key } = await params;
  if (!isModuleKey(key)) {
    notFound();
  }

  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const module = await getModuleForTenant(ctx.tenant.id, key);

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <ModulePanel module={module} />
    </div>
  );
}
