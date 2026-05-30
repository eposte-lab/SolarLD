'use client';

/**
 * CollapsibleFilters — client shell that hides a (server-rendered) set of
 * filter chips behind a compact "Filtri" toggle. Keeps list pages dense by
 * default while preserving the existing query-param filter UI untouched:
 * the page passes its filter groups as `children` and an `activeCount` so
 * the toggle can show how many filters are currently applied.
 *
 * Collapsed by default; auto-expands on mount when filters are already
 * active (so the user sees what's narrowing the list). A "Pulisci" reset
 * link is rendered when `resetHref` is provided and filters are active.
 */

import { useState } from 'react';
import Link from 'next/link';
import { SlidersHorizontal, ChevronDown } from 'lucide-react';

import { cn } from '@/lib/utils';

export function CollapsibleFilters({
  activeCount,
  resetHref,
  children,
}: {
  activeCount: number;
  resetHref?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(activeCount > 0);

  return (
    <div className="rounded-lg bg-surface-container-low">
      <div className="flex items-center justify-between px-3 py-2">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="inline-flex items-center gap-2 text-xs font-semibold text-on-surface-variant transition-colors hover:text-on-surface"
          aria-expanded={open}
        >
          <SlidersHorizontal size={14} strokeWidth={2.5} aria-hidden />
          Filtri
          {activeCount > 0 && (
            <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-bold tabular-nums text-on-primary">
              {activeCount}
            </span>
          )}
          <ChevronDown
            size={14}
            strokeWidth={2.5}
            aria-hidden
            className={cn('transition-transform', open && 'rotate-180')}
          />
        </button>
        {activeCount > 0 && resetHref && (
          <Link
            href={resetHref}
            className="text-[11px] font-semibold text-on-surface-variant hover:text-primary hover:underline"
          >
            Pulisci
          </Link>
        )}
      </div>
      {open && (
        <div className="flex flex-wrap gap-x-6 gap-y-3 px-3 pb-3 pt-1">
          {children}
        </div>
      )}
    </div>
  );
}
