'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

/**
 * Keeps the invii numbers live. Every `seconds` it soft-refreshes the route so
 * the server components re-read the DB (KPI strip, qualification report, table)
 * with fresh data — no manual reload. router.refresh() preserves scroll and
 * client state, and we skip the tick while the tab is hidden to avoid pointless
 * background polling. Renders nothing.
 */
export function AutoRefresh({ seconds = 60 }: { seconds?: number }) {
  const router = useRouter();

  useEffect(() => {
    const id = setInterval(() => {
      if (typeof document === 'undefined' || document.visibilityState === 'visible') {
        router.refresh();
      }
    }, seconds * 1000);
    return () => clearInterval(id);
  }, [router, seconds]);

  return null;
}
