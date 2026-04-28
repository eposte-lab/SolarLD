'use client';

/**
 * SideNav — Liquid Glass vertical rail (V2).
 *
 * Visual spec:
 *   - Sticky 256px rail su surface-container-lowest con liquid-glass
 *     subtle (backdrop-blur 20px) per dare profondità senza saturare
 *   - Brand lockup: monogram mint quadrato + wordmark SolarLead
 *   - Sezioni raggruppate (Acquisizione · Operatività · Setup) con
 *     micro eyebrow uppercase tra i gruppi
 *   - Active item: pill con bg-primary/10, left mint bar 2px, icona
 *     e label in primary; idle in on-surface-variant con hover bg-white/4
 *   - Footer: tenant card glass + sign-out
 *   - Icone Lucide professionali (24px stroke 1.75) — niente emoji
 */

import {
  ActivitySquare,
  BarChart3,
  Filter,
  Globe2,
  Home,
  Inbox,
  LineChart,
  Mail,
  type LucideIcon,
  Plus,
  Search,
  Send,
  Settings,
  ShieldCheck,
  Target,
  Users,
} from 'lucide-react';
import { BrandLogo } from '@/components/ui/brand-logo';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { GradientButton } from '@/components/ui/gradient-button';
import { SignOutButton } from '@/components/ui/sign-out-button';
import { cn } from '@/lib/utils';

export type NavIconKey =
  | 'dashboard'
  | 'leads'
  | 'campaigns'
  | 'contatti'
  | 'invii'
  | 'funnel'
  | 'territories'
  | 'analytics'
  | 'deliverability'
  | 'settings'
  | 'audiences'
  | 'experiments'
  | 'scoperta';

const ICON_MAP: Record<NavIconKey, LucideIcon> = {
  dashboard: Home,
  leads: Users,
  campaigns: Target,
  contatti: Inbox,
  invii: Send,
  funnel: Filter,
  territories: Globe2,
  analytics: BarChart3,
  deliverability: ShieldCheck,
  settings: Settings,
  audiences: Mail,
  experiments: ActivitySquare,
  scoperta: Search,
};

export interface NavItem {
  href: string;
  label: string;
  icon?: NavIconKey;
}

export interface NavSection {
  /** Section label rendered as eyebrow above its items. */
  label: string;
  items: NavItem[];
}

export interface SideNavProps {
  /** Either a flat list (legacy) or grouped sections (preferred). */
  items?: NavItem[];
  sections?: NavSection[];
  tenant: { business_name: string };
  user_email: string | null;
}

/**
 * Pick the single best-matching href for the current pathname:
 * the one that is equal to it, or — failing that — the longest
 * href that is a prefix of `${pathname}/`. This avoids the bug
 * where `/leads/follow-up` would highlight both `/leads` and
 * `/leads/follow-up` because `pathname.startsWith('/leads/')`
 * is true in both cases.
 */
function bestActiveHref(pathname: string, allHrefs: string[]): string | null {
  let best: string | null = null;
  for (const href of allHrefs) {
    if (href === '/') {
      if (pathname === '/') {
        if (!best || best.length < href.length) best = href;
      }
      continue;
    }
    if (pathname === href || pathname.startsWith(`${href}/`)) {
      if (!best || href.length > best.length) best = href;
    }
  }
  return best;
}

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon ? ICON_MAP[item.icon] : null;
  return (
    <li className="relative">
      {active && (
        <span
          className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-primary shadow-[0_0_12px_rgba(111,207,151,0.6)]"
          aria-hidden
        />
      )}
      <Link
        href={item.href}
        className={cn(
          'group flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-[13.5px] font-medium transition-all duration-200',
          active
            ? 'bg-primary/10 text-primary'
            : 'text-on-surface-variant hover:bg-white/[0.04] hover:text-on-surface',
        )}
      >
        {Icon && (
          <Icon
            size={18}
            strokeWidth={active ? 2 : 1.75}
            className="shrink-0"
            aria-hidden
          />
        )}
        <span className="tracking-tight">{item.label}</span>
      </Link>
    </li>
  );
}

export function SideNav({ items, sections, tenant, user_email }: SideNavProps) {
  const pathname = usePathname() ?? '/';

  // Normalize input: prefer sections, fall back to flat items.
  const renderSections: NavSection[] =
    sections ?? (items ? [{ label: 'Navigazione', items }] : []);

  // Resolve the single active href across all sections so that nested
  // routes (e.g. /leads/follow-up) don't also highlight their parent
  // (/leads).
  const allHrefs = renderSections.flatMap((s) => s.items.map((i) => i.href));
  const activeHref = bestActiveHref(pathname, allHrefs);

  return (
    <nav className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col bg-surface-container-lowest/80 backdrop-blur-glass-sm p-5 ghost-border md:flex">
      {/* Brand lockup */}
      <div className="mb-7 flex items-center gap-3 px-1.5">
        <div className="relative flex h-10 w-10 items-center justify-center rounded-2xl bg-primary/15 ghost-border-strong text-primary overflow-hidden">
          <BrandLogo size={22} title="SolarLead" />
          <span
            className="pointer-events-none absolute inset-0 bg-glass-specular opacity-70"
            aria-hidden
          />
        </div>
        <div className="leading-tight">
          <h1 className="font-headline text-[17px] font-bold tracking-tightest text-on-surface">
            SolarLead
          </h1>
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-on-surface-variant">
            Installer Pro
          </p>
        </div>
      </div>

      {/* Hero CTA */}
      <GradientButton href="/territories" size="md" className="mb-6 w-full justify-center">
        <Plus size={16} strokeWidth={2.25} aria-hidden />
        Nuovo territorio
      </GradientButton>

      {/* Grouped nav */}
      <div className="flex-1 overflow-y-auto -mx-1 px-1 space-y-5">
        {renderSections.map((section) => (
          <div key={section.label}>
            <p className="mb-2 px-3.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-on-surface-muted">
              {section.label}
            </p>
            <ul className="space-y-0.5">
              {section.items.map((item) => (
                <NavLink
                  key={item.href}
                  item={item}
                  active={item.href === activeHref}
                />
              ))}
            </ul>
          </div>
        ))}
      </div>

      {/* Tenant footer */}
      <div className="mt-5 rounded-2xl liquid-glass-sm p-4 relative overflow-hidden">
        <span
          className="pointer-events-none absolute inset-0 bg-glass-specular"
          aria-hidden
        />
        <div className="relative flex items-center gap-2.5 mb-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/15 text-primary shrink-0">
            <LineChart size={14} strokeWidth={2} aria-hidden />
          </div>
          <div className="min-w-0">
            <p className="truncate text-[13px] font-semibold text-on-surface leading-tight">
              {tenant.business_name}
            </p>
            {user_email && (
              <p className="truncate text-[11px] text-on-surface-variant leading-tight mt-0.5">
                {user_email}
              </p>
            )}
          </div>
        </div>
        <div className="relative">
          <SignOutButton />
        </div>
      </div>
    </nav>
  );
}
