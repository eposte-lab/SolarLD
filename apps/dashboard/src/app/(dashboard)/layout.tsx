import { redirect } from 'next/navigation';

import { IdleLogout } from '@/components/auth/idle-logout';
import { RealtimeToaster } from '@/components/realtime-toaster';
import { BackButton } from '@/components/ui/back-button';
import { NavigationProgress } from '@/components/ui/navigation-progress';
import { NotificationsBell } from '@/components/ui/notifications-bell';
import { SideNav, type NavSection } from '@/components/ui/side-nav';
import { getTopHotLead } from '@/lib/data/leads';
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

/**
 * Navigation è raggruppata per cluster di task:
 *   - Acquisizione → cosa entra (lead, contatti, territori)
 *   - Operatività  → cosa succede ai lead (panoramica, funnel, invii, deliverability)
 *   - Setup        → analytics + configurazione
 */
const NAV_SECTIONS: NavSection[] = [
  {
    label: 'Acquisizione',
    items: [
      { href: '/leads', label: 'Lead Attivi', icon: 'leads' },
      { href: '/leads/follow-up', label: 'Follow-up', icon: 'invii' },
      { href: '/contatti', label: 'Contatti', icon: 'contatti' },
      { href: '/scoperta', label: 'Trova aziende', icon: 'scoperta' },
      { href: '/email-templates', label: 'Template email', icon: 'email-templates' },
      { href: '/territorio', label: 'Territorio', icon: 'territories' },
    ],
  },
  {
    label: 'Operatività',
    items: [
      { href: '/', label: 'Panoramica', icon: 'dashboard' },
      { href: '/funnel', label: 'Funnel', icon: 'funnel' },
      { href: '/invii', label: 'Invii', icon: 'invii' },
      { href: '/ab-testing', label: 'Esperimenti A/B', icon: 'experiments' },
      { href: '/practices', label: 'Pratiche GSE', icon: 'pratiche' },
      { href: '/scadenze', label: 'Scadenze', icon: 'scadenze' },
      { href: '/deliverability', label: 'Deliverability', icon: 'deliverability' },
    ],
  },
  {
    label: 'Setup',
    items: [
      { href: '/settings', label: 'Impostazioni', icon: 'settings' },
    ],
  },
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

  // Onboarding gate + notifications + hot-lead hero CTA in parallel.
  // The wizard check must resolve before rendering (potential redirect);
  // notifications and hot-lead can fall back gracefully.
  let unread = 0;
  let recent: Awaited<ReturnType<typeof listRecentNotifications>> = [];
  let hotLead: Awaited<ReturnType<typeof getTopHotLead>> = null;
  const [pending] = await Promise.all([
    isOnboardingPending(ctx.tenant.id),
    Promise.all([countUnreadNotifications(), listRecentNotifications(20)])
      .then(([u, r]) => { unread = u; recent = r; })
      .catch(() => { /* bell degrades gracefully */ }),
    getTopHotLead()
      .then((l) => { hotLead = l; })
      .catch(() => { /* hero CTA degrades to fallback */ }),
  ]);

  if (pending) {
    redirect('/onboarding');
  }

  // Final gate: modules done but the installer hasn't confirmed the
  // territorial exclusivity yet — route them to the confirm step.
  if (isTerritoryConfirmPending(ctx.tenant)) {
    redirect('/onboarding/territory-confirm');
  }

  const baseSections = NAV_SECTIONS;

  // The previous super-admin "Demo Runs" section was tied to the old
  // execution system. Now that v3 runs through the standard funnel
  // pipeline, no separate admin surface is needed in the nav rail.
  const visibleSections: NavSection[] = baseSections;

  return (
    <div className="flex min-h-screen bg-surface">
      <NavigationProgress />
      <SideNav
        sections={visibleSections}
        tenant={{ business_name: ctx.tenant.business_name }}
        user_email={ctx.user_email}
        hotLead={hotLead}
      />
      <main className="flex-1 px-6 py-8 md:px-10">
        <div className="mx-auto max-w-[1400px]">
          <div className="mb-6 flex items-center gap-3">
            <BackButton />
            <div className="ml-auto">
              <NotificationsBell
                initialUnread={unread}
                initialItems={recent}
                tenantId={ctx.tenant.id}
              />
            </div>
          </div>
          {children}
        </div>
      </main>
      <RealtimeToaster tenantId={ctx.tenant.id} />
      {ctx.tenant.demo_device_limit_enabled ? (
        <IdleLogout
          idleMinutes={ctx.tenant.demo_device_idle_timeout_minutes ?? 30}
        />
      ) : null}
    </div>
  );
}
