import { redirect } from 'next/navigation';

import { getCurrentTenantContext } from '@/lib/data/tenant';

/**
 * Settings hub gate — closes the entire `/settings/*` tree for demo
 * tenants. The dashboard layout already hides the SideNav link; this
 * guard stops anyone who tries to deep-link directly (URL bar, bookmark
 * left over from a non-demo session, etc.) from reaching the page.
 *
 * Once we ship a customer-safe Settings UX, this guard can be replaced
 * with a per-subpath whitelist. For now: hard redirect to /leads.
 */
export default async function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const ctx = await getCurrentTenantContext();
  if (ctx?.tenant.is_demo) {
    redirect('/leads');
  }
  return <>{children}</>;
}
