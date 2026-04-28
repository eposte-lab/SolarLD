'use client';

/**
 * CollapsibleCard — BentoCard wrapper that can be opened / closed.
 *
 * Uses a <details>/<summary> pair (native HTML accordion) so it
 * works without any JS and is keyboard-accessible out of the box.
 * The chevron rotates 180° when open via a CSS transition.
 *
 * Props:
 *   title        — section heading (h2)
 *   label        — small uppercase label above the title
 *   badge        — optional pill shown next to the title (e.g. "3 invii")
 *   defaultOpen  — if true, the section starts expanded (default false)
 *   children     — section body
 */

import { type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/lib/utils';

interface CollapsibleCardProps {
  title: string;
  label?: string;
  badge?: string | number;
  defaultOpen?: boolean;
  children: ReactNode;
  className?: string;
}

export function CollapsibleCard({
  title,
  label,
  badge,
  defaultOpen = false,
  children,
  className,
}: CollapsibleCardProps) {
  return (
    <details
      open={defaultOpen}
      className={cn(
        'group rounded-2xl bg-surface-container-low ring-1 ring-inset ring-outline-variant/20',
        'shadow-ambient-sm',
        className,
      )}
    >
      <summary
        className={cn(
          'flex cursor-pointer select-none list-none items-center justify-between',
          'rounded-2xl px-6 py-5',
          'hover:bg-surface-container transition-colors duration-150',
          // Remove the default disclosure triangle in all browsers
          '[&::-webkit-details-marker]:hidden',
        )}
      >
        <div className="min-w-0">
          {label && (
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              {label}
            </p>
          )}
          <div className="flex items-center gap-2">
            <h2 className="font-headline text-xl font-bold tracking-tighter text-on-surface">
              {title}
            </h2>
            {badge !== undefined && badge !== '' && (
              <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold text-on-surface-variant">
                {badge}
              </span>
            )}
          </div>
        </div>
        <ChevronDown
          size={18}
          strokeWidth={2}
          aria-hidden
          className="shrink-0 text-on-surface-variant transition-transform duration-200 group-open:rotate-180"
        />
      </summary>

      {/* Body — only visible when open */}
      <div className="px-6 pb-6">
        {children}
      </div>
    </details>
  );
}
