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
  CalendarClock,
  Filter,
  FolderOpen,
  Globe2,
  Home,
  Inbox,
  LineChart,
  Loader2,
  Mail,
  type LucideIcon,
  Search,
  Send,
  Settings,
  ShieldCheck,
  Target,
  Terminal,
  Users,
} from 'lucide-react';
import Link, { useLinkStatus } from 'next/link';
import { usePathname } from 'next/navigation';

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
  | 'scoperta'
  | 'pratiche'
  | 'scadenze'
  | 'admin'
  | 'email-templates';

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
  // GSE Practices module — folder icon evokes burocrazia/document
  // archive better than a generic doc icon.
  pratiche: FolderOpen,
  // Scadenze GSE — calendar-clock evokes regulatory SLA deadlines.
  scadenze: CalendarClock,
  // Super-admin internal tooling — terminal icon signals dev/ops surface.
  admin: Terminal,
  // Custom email templates for generic_outreach campaigns.
  'email-templates': Mail,
};

export interface NavItem {
  href: string;
  label: string;
  icon?: NavIconKey;
  /**
   * Sotto-voci del cluster. Renderizzate indentate sotto il parent,
   * sempre visibili (niente collapse) per non nascondere route. I loro
   * href partecipano alla risoluzione dell'highlight attivo.
   */
  children?: NavItem[];
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

function NavLink({
  item,
  activeHref,
}: {
  item: NavItem;
  activeHref: string | null;
}) {
  const Icon = item.icon ? ICON_MAP[item.icon] : null;
  const children = item.children ?? [];
  return (
    <li className="relative">
      <Link href={item.href} className="block">
        <NavLinkBody
          Icon={Icon}
          active={item.href === activeHref}
          label={item.label}
        />
      </Link>
      {children.length > 0 && (
        <ul className="mt-0.5 ml-[26px] space-y-0.5 border-l border-outline-variant/30 pl-2.5">
          {children.map((child) => {
            const ChildIcon = child.icon ? ICON_MAP[child.icon] : null;
            return (
              <li key={child.href} className="relative">
                <Link href={child.href} className="block">
                  <NavLinkBody
                    Icon={ChildIcon}
                    active={child.href === activeHref}
                    label={child.label}
                    nested
                  />
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </li>
  );
}

/**
 * Body del NavLink: `useLinkStatus` (Next 15) ci dà subito lo stato
 * "navigazione partita" senza aspettare il cambio URL. Così appena
 * clicchi il link si accende il pill attivo + spinner — niente più
 * sensazione di "ho premuto ma non succede nulla per 1-2 secondi".
 * Deve stare DENTRO il `<Link>`, il hook lo richiede.
 */
function NavLinkBody({
  Icon,
  active,
  label,
  nested = false,
}: {
  Icon: LucideIcon | null;
  active: boolean;
  label: string;
  nested?: boolean;
}) {
  const { pending } = useLinkStatus();
  const highlighted = active || pending;
  return (
    <span
      className={cn(
        'group relative flex items-center gap-3 rounded-xl font-medium transition-all duration-200',
        nested ? 'px-3 py-2 text-[12.5px]' : 'px-3.5 py-2.5 text-[13.5px]',
        highlighted
          ? 'bg-primary/10 text-primary'
          : 'text-on-surface-variant hover:bg-white/[0.04] hover:text-on-surface',
      )}
    >
      {highlighted && (
        <span
          className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-primary shadow-[0_0_12px_rgba(34,197,94,0.6)]"
          aria-hidden
        />
      )}
      {Icon && (
        <Icon
          size={nested ? 16 : 18}
          strokeWidth={highlighted ? 2 : 1.75}
          className="shrink-0"
          aria-hidden
        />
      )}
      <span className="flex-1 tracking-tight">{label}</span>
      {pending && (
        <Loader2 size={13} className="shrink-0 animate-spin" aria-hidden />
      )}
    </span>
  );
}

/**
 * NavGroups — render condiviso dell'albero nav (sezioni → cluster →
 * children) con risoluzione dell'highlight attivo. Usato sia dalla
 * SideNav desktop sia dal drawer mobile, così la config nav vive in un
 * solo posto e i due restano in sync.
 */
export function NavGroups({
  sections,
  className,
}: {
  sections: NavSection[];
  className?: string;
}) {
  const pathname = usePathname() ?? '/';

  // Resolve the single active href across all sections so that nested
  // routes (e.g. /leads/follow-up) don't also highlight their parent
  // (/leads). Children hrefs MUST participate too, otherwise a sub-item
  // route would highlight its parent cluster instead of itself.
  const allHrefs = sections.flatMap((s) =>
    s.items.flatMap((i) => [i.href, ...(i.children ?? []).map((c) => c.href)]),
  );
  const activeHref = bestActiveHref(pathname, allHrefs);

  return (
    <div className={cn('space-y-5', className)}>
      {sections.map((section) => (
        <div key={section.label}>
          <p className="mb-2 px-3.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-on-surface-muted">
            {section.label}
          </p>
          <ul className="space-y-0.5">
            {section.items.map((item) => (
              <NavLink key={item.href} item={item} activeHref={activeHref} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

export function SideNav({
  items,
  sections,
  tenant,
  user_email,
}: SideNavProps) {
  // Normalize input: prefer sections, fall back to flat items.
  const renderSections: NavSection[] =
    sections ?? (items ? [{ label: 'Navigazione', items }] : []);

  return (
    <nav className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col bg-surface-container-lowest/80 backdrop-blur-glass-sm p-5 ghost-border md:flex">
      {/* Brand lockup — co-brand Total Trade: albero multicolore (legge
          su qualsiasi sfondo) + wordmark "Solar Trade Lead". */}
      <div className="mb-7 flex items-center gap-2.5 px-1.5">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/total-trade-mark.png"
          alt="Total Trade"
          className="h-10 w-auto shrink-0"
        />
        <div className="leading-tight">
          <h1 className="font-headline text-[17px] font-bold tracking-tightest text-on-surface">
            Solar Trade Lead
          </h1>
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-on-surface-variant">
            Installer Pro
          </p>
        </div>
      </div>

      {/* Grouped nav */}
      <NavGroups
        sections={renderSections}
        className="mt-1 flex-1 overflow-y-auto -mx-1 px-1"
      />

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
