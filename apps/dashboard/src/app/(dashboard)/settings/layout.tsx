import { headers } from 'next/headers';
import { redirect } from 'next/navigation';

import { getCurrentTenantContext } from '@/lib/data/tenant';

/**
 * Settings hub gate — closes the `/settings/*` tree for demo tenants
 * EXCEPT for a small whitelist of pages that are safe (and useful) to
 * keep open. Right now the only exception is `/settings/pipeline-test`
 * — the super_admin smoke-test panel that injects a synthetic candidate
 * into the funnel. We need it accessible from the demo workspace so
 * we can populate a real-looking lead card on demand.
 *
 * The dashboard layout already hides the SideNav link; this guard stops
 * anyone who tries to deep-link directly into a non-whitelisted page.
 */
const DEMO_WHITELIST = ['/settings/pipeline-test'];

export default async function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const ctx = await getCurrentTenantContext();
  if (ctx?.tenant.is_demo) {
    // App Router doesn't expose pathname on the server outside of
    // middleware; we read `x-invoke-path` (set by Next.js) or fall
    // back to `next-url` / `referer`. If none match, the safest move
    // is to deny — the whitelist is small and known.
    // The middleware injects `x-pathname` so server components can
    // tell which route is being rendered (App Router doesn't expose
    // pathname server-side natively). See `lib/supabase/middleware.ts`.
    const h = await headers();
    const pathname = h.get('x-pathname') ?? '';
    const allowed = DEMO_WHITELIST.some((p) => pathname.startsWith(p));
    if (!allowed) {
      redirect('/leads');
    }
  }
  return <>{children}</>;
}
