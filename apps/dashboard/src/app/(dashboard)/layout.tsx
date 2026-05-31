import { redirect } from 'next/navigation';

import { IdleLogout } from '@/components/auth/idle-logout';
import { RealtimeToaster } from '@/components/realtime-toaster';
import { BackButton } from '@/components/ui/back-button';
import { MobileNav } from '@/components/ui/mobile-nav';
import { NavigationProgress } from '@/components/ui/navigation-progress';
import { NotificationsBell } from '@/components/ui/notifications-bell';
import { SideNav, type NavSection } from '@/components/ui/side-nav';
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
 * Navigation consolidata in cluster a 2 livelli (audit UX P-nav): da 12
 * voci flat a 7 cluster top-level. Le route correlate diventano
 * sotto-voci (`children`) indentate sotto il parent — tutte le route
 * restano raggiungibili in un click, nessuna pagina rimossa.
 *
 *   Operatività
 *     - Panoramica
 *     - Lead          → Follow-up, Contatti
 *     - Territorio     → Trova aziende
 *     - Invii          → Template email, Esperimenti A/B
 *     - Funnel
 *     - Deliverability
 *   Setup
 *     - Impostazioni
 *
 * Servizio "Pratiche GSE" + "Scadenze" resta archiviato (voci non
 * montate); le route /practices, /scadenze e il flusso pratica restano
 * nel repo per riattivarlo all'occorrenza.
 */
const NAV_SECTIONS: NavSection[] = [
  {
    label: 'Operatività',
    items: [
      { href: '/', label: 'Panoramica', icon: 'dashboard' },
      {
        href: '/leads',
        label: 'Lead',
        icon: 'leads',
        children: [
          { href: '/leads/follow-up', label: 'Follow-up', icon: 'scadenze' },
          { href: '/contatti', label: 'Contatti', icon: 'contatti' },
        ],
      },
      {
        href: '/territorio',
        label: 'Territorio',
        icon: 'territories',
        children: [
          { href: '/scoperta', label: 'Trova aziende', icon: 'scoperta' },
        ],
      },
      {
        href: '/invii',
        label: 'Invii',
        icon: 'invii',
        children: [
          { href: '/email-templates', label: 'Template email', icon: 'email-templates' },
          { href: '/ab-testing', label: 'Esperimenti A/B', icon: 'experiments' },
        ],
      },
      { href: '/funnel', label: 'Funnel', icon: 'funnel' },
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

  // Onboarding gate + notifications in parallel. The wizard check must
  // resolve before rendering (potential redirect); notifications can
  // fall back gracefully.
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

  // A moderated trial tenant keeps the FULL nav. The moderation gate is
  // on the contatto → lead STATE promotion (enforced in the lead-surface
  // queries in lib/data/leads.ts), not on hiding pages: the tenant must be
  // able to browse its contatti and open the schede of the IDs it was
  // sent. The /leads list simply stays empty until the operator promotes
  // an engaged contatto to a lead.
  //
  // The previous super-admin "Demo Runs" section was tied to the old
  // execution system. Now that v3 runs through the standard funnel
  // pipeline, no separate admin surface is needed in the nav rail.
  const visibleSections: NavSection[] = NAV_SECTIONS;

  return (
    <div className="flex min-h-screen bg-surface">
      <NavigationProgress />
      <SideNav
        sections={visibleSections}
        tenant={{ business_name: ctx.tenant.business_name }}
        user_email={ctx.user_email}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Shell mobile: top-bar + drawer (md:hidden internamente) */}
        <MobileNav
          sections={visibleSections}
          tenant={{ business_name: ctx.tenant.business_name }}
          user_email={ctx.user_email}
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
      </div>
      <RealtimeToaster tenantId={ctx.tenant.id} />
      {ctx.tenant.demo_device_limit_enabled ? (
        <IdleLogout
          idleMinutes={ctx.tenant.demo_device_idle_timeout_minutes ?? 30}
        />
      ) : null}
    </div>
  );
}
