'use client';

import { useEffect, useRef } from 'react';
import { API_URL } from '@/lib/api';

/**
 * Fire-and-forget visit ping.
 *
 * Uses `navigator.sendBeacon` when available (keeps working even if
 * the page unloads immediately after render) and falls back to a
 * no-credentials `fetch` otherwise. The backend endpoint is itself
 * idempotent: it only flips `dashboard_visited_at` from NULL and
 * advances pipeline_status if the lead is still in a silent state.
 */
export function VisitTracker({ slug }: { slug: string }) {
  const fired = useRef(false);

  useEffect(() => {
    if (fired.current) return;
    fired.current = true;
    const url = `${API_URL}/v1/public/lead/${encodeURIComponent(slug)}/visit`;
    try {
      if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
        // Beacon expects a Blob; empty body is fine.
        navigator.sendBeacon(url, new Blob([], { type: 'application/json' }));
        return;
      }
    } catch {
      // fall through to fetch
    }
    void fetch(url, { method: 'POST', keepalive: true }).catch(() => undefined);
  }, [slug]);

  return null;
}
