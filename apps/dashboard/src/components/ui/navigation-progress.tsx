'use client';

/**
 * NavigationProgress — thin animated bar at the top of the viewport
 * that fires whenever the user clicks an internal link or triggers a
 * router navigation. Provides the "something is happening" feedback
 * that Next.js' default behaviour lacks for query-string changes
 * (where loading.tsx doesn't fire because the route segment hasn't
 * changed). Clicking between tabs like /leads ↔ /leads?mode=hot used
 * to look frozen for ~200-400ms with zero visual hint.
 *
 * Implementation: listen for clicks on internal <a> elements at the
 * document level, start the bar, then clear it when pathname or
 * searchParams transitions to the new value. No dependencies.
 */

import { usePathname, useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

export function NavigationProgress() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [active, setActive] = useState(false);
  const lastKey = useRef<string>(`${pathname}?${searchParams?.toString() ?? ''}`);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Click interceptor — fires on every internal anchor click before
  // Next.js router takes over.
  useEffect(() => {
    function onClick(e: MouseEvent) {
      // Only respond to plain left-clicks on links (no modifier keys
      // — those open new tabs and we don't want to flash for those).
      if (
        e.defaultPrevented ||
        e.button !== 0 ||
        e.metaKey ||
        e.ctrlKey ||
        e.shiftKey ||
        e.altKey
      )
        return;
      const target = e.target as HTMLElement | null;
      const anchor = target?.closest('a');
      if (!anchor) return;
      const href = anchor.getAttribute('href');
      if (!href) return;
      // Skip external + hash + tel/mailto anchors.
      if (
        href.startsWith('http://') ||
        href.startsWith('https://') ||
        href.startsWith('#') ||
        href.startsWith('mailto:') ||
        href.startsWith('tel:') ||
        anchor.target === '_blank'
      )
        return;
      // Skip clicks on the link that's already current.
      const current = `${pathname}?${searchParams?.toString() ?? ''}`;
      if (href === pathname || href === current) return;
      setActive(true);
      // Safety net: if the route never changes (404, click cancelled,
      // hash-only link) hide the bar after 4s so it doesn't get stuck.
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setActive(false), 4000);
    }
    document.addEventListener('click', onClick);
    return () => document.removeEventListener('click', onClick);
  }, [pathname, searchParams]);

  // Hide the bar once the URL actually transitions.
  useEffect(() => {
    const key = `${pathname}?${searchParams?.toString() ?? ''}`;
    if (key !== lastKey.current) {
      lastKey.current = key;
      // Small delay so the bar finishes the visual sweep instead of
      // popping out instantly when the page is fast-cached.
      const t = setTimeout(() => setActive(false), 150);
      return () => clearTimeout(t);
    }
  }, [pathname, searchParams]);

  return (
    <div
      aria-hidden
      className="pointer-events-none fixed left-0 right-0 top-0 z-[100] h-0.5 overflow-hidden"
    >
      <div
        className={`h-full bg-primary transition-[width,opacity] ease-out ${
          active
            ? 'w-[90%] opacity-100 duration-[1500ms]'
            : 'w-0 opacity-0 duration-200'
        }`}
        style={{ boxShadow: '0 0 8px rgba(111,207,151,0.6)' }}
      />
    </div>
  );
}
