'use client';

/**
 * SideNavBar — Luminous Curator vertical rail (Fase B).
 *
 * Visual spec (from stitch mockup `solarlead_overview_bento`):
 *   - White surface floating against the f4f7f6 body bg
 *   - Right-side rounded-xl, with the rail shadow bleeding rightward
 *   - Active item: primary background + ambient white ring
 *   - Idle item: on-surface-variant text, subtle hover translate-x
 *   - Top: brand lockup, then a primary gradient "Nuovo progetto" CTA
 *   - Bottom: tenant card + sign-out
 *
 * The route highlight relies on `usePathname`; everything else is
 * presentational and safe to render as a server child if we ever
 * decide to pre-render the nav shell.
 */

import Link, { type LinkProps } from 'next/link';
import { usePathname } from 'next/navigation';

import { GradientButton } from '@/components/ui/gradient-button';
import { SignOutButton } from '@/components/ui/sign-out-button';
import { cn } from '@/lib/utils';

export interface NavItem {
  href: string;
  label: string;
  /** Material Symbols Outlined ligature name. */
  icon?: string;
}

export interface SideNavProps {
  items: NavItem[];
  tenant: { business_name: string };
  user_email: string | null;
}

/**
 * Minimal inline SVG icon set — avoids pulling the full Material
 * Symbols font for 6 glyphs. Each returns a 20px icon suitable for
 * a row in the rail.
 */
function NavIcon({ name }: { name: string }) {
  const common = 'h-5 w-5 shrink-0';
  switch (name) {
    case 'dashboard':
      return (
        <svg viewBox="0 0 24 24" className={common} fill="currentColor">
          <path d="M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z" />
        </svg>
      );
    case 'leads':
      return (
        <svg viewBox="0 0 24 24" className={common} fill="currentColor">
          <path d="M12 12a4 4 0 100-8 4 4 0 000 8zm0 2c-3.33 0-8 1.67-8 5v3h16v-3c0-3.33-4.67-5-8-5z" />
        </svg>
      );
    case 'campaigns':
      return (
        <svg viewBox="0 0 24 24" className={common} fill="currentColor">
          <path d="M3 10v4h3l5 5V5L6 10H3zm13.5 2a4.5 4.5 0 00-2.5-4v8a4.5 4.5 0 002.5-4zM14 3.2v2.1a7 7 0 010 13.4v2.1a9 9 0 000-17.6z" />
        </svg>
      );
    case 'territories':
      return (
        <svg viewBox="0 0 24 24" className={common} fill="currentColor">
          <path d="M12 2C7.6 2 4 5.6 4 10c0 5.5 8 12 8 12s8-6.5 8-12c0-4.4-3.6-8-8-8zm0 11a3 3 0 110-6 3 3 0 010 6z" />
        </svg>
      );
    case 'analytics':
      return (
        <svg viewBox="0 0 24 24" className={common} fill="currentColor">
          <path d="M4 20h4v-8H4v8zm6 0h4V4h-4v16zm6 0h4v-12h-4v12z" />
        </svg>
      );
    case 'settings':
      return (
        <svg viewBox="0 0 24 24" className={common} fill="currentColor">
          <path d="M19.14 12.94a7.14 7.14 0 000-1.88l2.03-1.58a.5.5 0 00.12-.64l-1.92-3.32a.5.5 0 00-.6-.22l-2.39.96a7.1 7.1 0 00-1.62-.94l-.36-2.54a.5.5 0 00-.5-.43h-3.84a.5.5 0 00-.5.43l-.36 2.54a7.1 7.1 0 00-1.62.94l-2.39-.96a.5.5 0 00-.6.22L2.71 8.84a.5.5 0 00.12.64l2.03 1.58a7.14 7.14 0 000 1.88L2.83 14.52a.5.5 0 00-.12.64l1.92 3.32a.5.5 0 00.6.22l2.39-.96a7.1 7.1 0 001.62.94l.36 2.54a.5.5 0 00.5.43h3.84a.5.5 0 00.5-.43l.36-2.54a7.1 7.1 0 001.62-.94l2.39.96a.5.5 0 00.6-.22l1.92-3.32a.5.5 0 00-.12-.64l-2.03-1.58zM12 15.5a3.5 3.5 0 110-7 3.5 3.5 0 010 7z" />
        </svg>
      );
    default:
      return <span className={common} aria-hidden />;
  }
}

function isActive(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SideNav({ items, tenant, user_email }: SideNavProps) {
  const pathname = usePathname() ?? '/';

  return (
    <nav className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col rounded-r-xl bg-surface-container-lowest p-6 shadow-rail md:flex">
      {/* Brand lockup */}
      <div className="mb-8 flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-gradient-primary text-on-primary">
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="currentColor">
            <path d="M12 18a6 6 0 100-12 6 6 0 000 12zm0-17v3M12 20v3M4.22 4.22l2.12 2.12M17.66 17.66l2.12 2.12M1 12h3M20 12h3M4.22 19.78l2.12-2.12M17.66 6.34l2.12-2.12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" fill="none" />
          </svg>
        </div>
        <div className="leading-tight">
          <h1 className="font-headline text-xl font-extrabold tracking-tighter text-primary">
            SolarLead
          </h1>
          <p className="text-[11px] font-medium text-on-surface-variant">
            Installer Pro
          </p>
        </div>
      </div>

      {/* Hero CTA */}
      <GradientButton href="/territories" size="md" className="mb-6 w-full">
        <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor">
          <path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z" />
        </svg>
        Nuovo territorio
      </GradientButton>

      {/* Nav items */}
      <ul className="flex-1 space-y-1">
        {items.map((item) => {
          const active = isActive(pathname, item.href);
          return (
            <li key={item.href}>
              <Link
                href={item.href as LinkProps<string>['href']}
                className={cn(
                  'group flex items-center gap-3 rounded-xl px-4 py-2.5 text-sm font-semibold transition-all duration-150',
                  active
                    ? 'bg-primary text-on-primary shadow-ambient-sm'
                    : 'text-on-surface-variant hover:translate-x-0.5 hover:bg-surface-container-low hover:text-primary',
                )}
              >
                {item.icon && <NavIcon name={item.icon} />}
                <span>{item.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>

      {/* Tenant footer */}
      <div className="mt-4 rounded-xl bg-surface-container-low p-4">
        <p className="truncate text-sm font-semibold text-on-surface">
          {tenant.business_name}
        </p>
        {user_email && (
          <p className="truncate text-xs text-on-surface-variant">
            {user_email}
          </p>
        )}
        <div className="mt-3">
          <SignOutButton />
        </div>
      </div>
    </nav>
  );
}
