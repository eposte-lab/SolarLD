import { redirect } from 'next/navigation';

import { RealtimeToaster } from '@/components/realtime-toaster';
import { NotificationsBell } from '@/components/ui/notifications-bell';
import { SideNav, type NavItem } from '@/components/ui/side-nav';
import {
  countUnreadNotifications,
  listRecentNotifications,
} from '@/lib/data/notifications';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import {
  isOnboardingPending,
  isTerritoryConfirmPending,
} from '@/lib/data/tenantConfig';
import { createSupabaseServerClient } from '@/lib/supabase/server';

/**
 * AppShell — two-column layout: fixed `SideNav` rail + fluid `<main>`.
 *
 * Visuals follow DESIGN.md:
 *   - Body bg: `surface` (#f4f7f6)
 *   - Content region: 32px external padding, max-width 1400px
 *   - No 1px borders anywhere — separation is purely tonal
 */

const NAV: NavItem[] = [
  { href: '/', label: 'Panoramica', icon: 'dashboard' },
  { href: '/contatti', label: 'Contatti', icon: 'contatti' },
  { href: '/leads', label: 'Lead Attivi', icon: 'leads' },
  { href: '/invii', label: 'Invii', icon: 'invii' },
  { href: '/territories', label: 'Territori', icon: 'territories' },
  { href: '/funnel', label: 'Funnel', icon: 'funnel' },
  { href: '/analytics', label: 'Analytics', icon: 'analytics' },
  { href: '/settings', label: 'Impostazioni', icon: 'settings' },
];

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const ctx = await getCurrentTenantContext();

  if (!ctx) {
    // Distinguish: no Supabase session at all vs session but no tenant row.
    // The middleware already redirects un-authed users for protected routes,
    // but this guard handles the root ("/") and edge cases.
    const supabase = await createSupabaseServerClient();
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) {
      // Truly unauthenticated → login form.
      redirect('/login');
    } else {
      // Authenticated but no tenant_members row → setup helper page.
      // Redirecting to /login here would loop: middleware lets them
      // through (they're logged in) but the layout bounces them back.
      redirect('/no-tenant');
    }
  }

  // Onboarding gate + notifications in parallel — no dependency between
  // them, so fire both at once. The wizard check must resolve before
  // rendering (potential redirect), notifications can fall back to zero.
  let unread = 0;
  let recent: Awaited<ReturnType<typeof listRecentNotifications>> = [];
  const [pending] = await Promise.all([
    isOnboardingPending(ctx.tenant.id),
    Promise.all([countUnreadNotifications(), listRecentNotifications(20)])
      .then(([u, r]) => { unread = u; recent = r; })
      .catch(() => { /* bell degrades gracefully */ }),
  ]);

  if (pending) {
    redirect('/onboarding');
  }

  // Final gate: modules done but the installer hasn't confirmed the
  // territorial exclusivity yet — route them to the confirm step.
  if (isTerritoryConfirmPending(ctx.tenant)) {
    redirect('/onboarding/territory-confirm');
  }

  return (
    <div className="flex min-h-screen bg-surface">
      <SideNav
        items={NAV}
        tenant={{ business_name: ctx.tenant.business_name }}
        user_email={ctx.user_email}
      />
      <main className="flex-1 px-6 py-8 md:px-10">
        <div className="mx-auto max-w-[1400px]">
          <div className="mb-6 flex justify-end">
            <NotificationsBell
              initialUnread={unread}
              initialItems={recent}
              tenantId={ctx.tenant.id}
            />
          </div>
          {children}
        </div>
      </main>
      <RealtimeToaster tenantId={ctx.tenant.id} />
    </div>
  );
}
