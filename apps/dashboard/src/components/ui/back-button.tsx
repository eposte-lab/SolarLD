'use client';

/**
 * BackButton — shows a chevron-left arrow on any page that is not a
 * top-level nav destination. Calls router.back() so the user returns
 * to wherever they came from, respecting the full history stack.
 *
 * Top-level paths (no back button):
 *   / · /leads · /invii · /contatti · /scoperta · /territories
 *   /funnel · /analytics · /deliverability · /settings · /experiments
 *   /campaigns · /funnel
 *
 * Everything else (sub-pages, detail pages, settings sub-sections)
 * gets the arrow automatically.
 */

import { ChevronLeft } from 'lucide-react';
import { usePathname, useRouter } from 'next/navigation';

/** Paths where the back button should NOT appear. */
const TOP_LEVEL = new Set([
  '/',
  '/leads',
  '/leads/follow-up',
  '/invii',
  '/contatti',
  '/scoperta',
  '/territories',
  '/funnel',
  '/analytics',
  '/deliverability',
  '/settings',
  '/experiments',
  '/campaigns',
]);

export function BackButton() {
  const pathname = usePathname() ?? '/';
  const router = useRouter();

  if (TOP_LEVEL.has(pathname)) return null;

  return (
    <button
      type="button"
      onClick={() => router.back()}
      aria-label="Torna indietro"
      className="
        inline-flex items-center gap-1.5
        rounded-xl
        px-3 py-1.5
        text-[13px] font-medium
        text-on-surface-variant
        hover:bg-surface-container
        hover:text-on-surface
        active:scale-95
        transition-all duration-150
        -ml-1
      "
    >
      <ChevronLeft size={16} strokeWidth={2} aria-hidden />
      <span>Indietro</span>
    </button>
  );
}
