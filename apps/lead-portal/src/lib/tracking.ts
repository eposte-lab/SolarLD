/**
 * Portal engagement beacon — Part B.1 deep-tracking.
 *
 * Transforms the public lead-portal page into a "heat sensor": every
 * scroll milestone, CTA hover, video event, and a 15s heartbeat while
 * the page is visible goes to ``POST /v1/public/portal/track``.
 *
 * The endpoint returns 204 unconditionally (soft-fail on anything that
 * isn't a schema error) so we use ``navigator.sendBeacon`` where the
 * browser exposes it — it survives the page unload that ``fetch`` does
 * not (mobile Safari especially kills in-flight fetches on navigate).
 *
 * Contract (keep in lockstep with
 * ``apps/api/src/routes/public.py::PortalTrackEvent`` and
 * ``_ALLOWED_EVENT_KINDS``):
 *
 *     {
 *       slug, session_id, event_kind, metadata, elapsed_ms
 *     }
 *
 * Session semantics:
 *   * ``session_id`` is a UUID generated on first load and cached in
 *     ``sessionStorage`` so a tab refresh keeps the same session but
 *     reopening the link in a new tab starts a new one. The rollup
 *     (``engagement_service.run_engagement_rollup``) counts distinct
 *     session_ids as "visits".
 *   * ``elapsed_ms`` is measured from the portal.view event, not from
 *     page load — the view is the t=0 anchor and the first fire after
 *     the script mounts.
 *
 * Heartbeat cadence (15s) matches ``HEARTBEAT_INTERVAL_SEC`` in the
 * Python rollup. Changing it here requires changing it there too or
 * the "time on page" total will be off by a constant factor.
 */

import { API_URL } from './api';

/** Keep in sync with ``_ALLOWED_EVENT_KINDS`` in routes/public.py. */
export type PortalEventKind =
  | 'portal.view'
  | 'portal.scroll_50'
  | 'portal.scroll_90'
  | 'portal.roi_viewed'
  | 'portal.cta_hover'
  | 'portal.whatsapp_click'
  | 'portal.appointment_click'
  | 'portal.video_play'
  | 'portal.video_complete'
  | 'portal.heartbeat'
  | 'portal.leave';

const SESSION_STORAGE_KEY = 'solarLead.portal.session_id';

/**
 * Per-tab session id. Read/initialised lazily — accessing
 * ``sessionStorage`` at module load breaks SSR-safe imports.
 */
function getOrCreateSessionId(): string {
  if (typeof window === 'undefined') return '';
  try {
    const existing = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (existing) return existing;
    const next =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, next);
    return next;
  } catch {
    // Private-mode Safari throws on sessionStorage access. Fall back
    // to an in-memory id — it only lives for the current module, which
    // is good enough to link heartbeats within one page.
    return (
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`
    );
  }
}

export type TrackerHandle = {
  /** Emit an ad-hoc event (CTA click, video_complete, ...). */
  track: (kind: PortalEventKind, metadata?: Record<string, unknown>) => void;
  /** Tear down listeners — called by React cleanup. */
  stop: () => void;
  /** For tests / debugging. */
  sessionId: string;
};

type StartOptions = {
  /** Element to observe for scroll milestones + ROI viewport (defaults to document). */
  scrollRoot?: HTMLElement | null;
  /** Specific element that, when ≥50% visible, fires ``portal.roi_viewed``. */
  roiSectionEl?: HTMLElement | null;
  /** Hero video element — wires play/ended → portal.video_* events. */
  videoEl?: HTMLVideoElement | null;
  /** Elements that count as CTA hovers (WhatsApp card + Appointment form). */
  ctaEls?: Array<HTMLElement | null>;
  /** Override the heartbeat interval (ms) for tests. */
  heartbeatIntervalMs?: number;
};

/**
 * Start the beacon. Idempotent-safe-ish: the caller (React effect) is
 * responsible for calling ``stop()`` on unmount. Firing twice would
 * create duplicate listeners; the component that owns this only
 * mounts once per page.
 *
 * Returns a handle with a manual ``track()`` for ad-hoc events (the
 * WhatsApp CTA and Appointment form already post their own business
 * events — they use this to add engagement telemetry on top).
 */
export function startPortalTracker(
  slug: string,
  opts: StartOptions = {},
): TrackerHandle {
  const sessionId = getOrCreateSessionId();
  const t0 = performance.now();
  const heartbeatMs = opts.heartbeatIntervalMs ?? 15_000;

  // Track which one-shot milestones we've already sent so a long
  // scroll doesn't spam the beacon.
  const fired: Partial<Record<PortalEventKind, true>> = {};

  const send = (kind: PortalEventKind, metadata: Record<string, unknown> = {}) => {
    if (!sessionId) return; // SSR guard
    const body = JSON.stringify({
      slug,
      session_id: sessionId,
      event_kind: kind,
      metadata,
      elapsed_ms: Math.max(0, Math.round(performance.now() - t0)),
    });
    const url = `${API_URL}/v1/public/portal/track`;
    try {
      if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
        navigator.sendBeacon(
          url,
          new Blob([body], { type: 'application/json' }),
        );
        return;
      }
    } catch {
      // fall through
    }
    void fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: true,
    }).catch(() => undefined);
  };

  const sendOnce = (
    kind: PortalEventKind,
    metadata: Record<string, unknown> = {},
  ) => {
    if (fired[kind]) return;
    fired[kind] = true;
    send(kind, metadata);
  };

  // ---- portal.view: the t=0 anchor ----
  send('portal.view', {
    referrer: typeof document !== 'undefined' ? document.referrer || null : null,
  });

  // ---- scroll milestones (50% / 90%) ----
  const onScroll = () => {
    if (typeof window === 'undefined' || typeof document === 'undefined') return;
    const doc = document.documentElement;
    const scrollTop = window.scrollY || doc.scrollTop || 0;
    const viewport = window.innerHeight || doc.clientHeight || 0;
    const full = doc.scrollHeight - viewport;
    if (full <= 0) return;
    const pct = Math.min(100, Math.max(0, (scrollTop / full) * 100));
    if (pct >= 50) sendOnce('portal.scroll_50', { pct: Math.round(pct) });
    if (pct >= 90) sendOnce('portal.scroll_90', { pct: Math.round(pct) });
  };
  window.addEventListener('scroll', onScroll, { passive: true });

  // ---- ROI viewport ----
  let roiObserver: IntersectionObserver | null = null;
  if (opts.roiSectionEl && typeof IntersectionObserver !== 'undefined') {
    roiObserver = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting && e.intersectionRatio >= 0.5) {
            sendOnce('portal.roi_viewed');
            roiObserver?.disconnect();
            break;
          }
        }
      },
      { threshold: [0.5] },
    );
    roiObserver.observe(opts.roiSectionEl);
  }

  // ---- CTA hovers (throttled to once per element via sendOnce key trick) ----
  const ctaAbort = new AbortController();
  const ctaHovers: Array<HTMLElement> = [];
  for (const el of opts.ctaEls ?? []) {
    if (!el) continue;
    ctaHovers.push(el);
    let localFired = false;
    el.addEventListener(
      'mouseenter',
      () => {
        if (localFired) return;
        localFired = true;
        // NOT sendOnce — multiple CTAs can each fire once, and the
        // Python cap prevents over-scoring.
        send('portal.cta_hover', {
          target: el.dataset?.cta ?? el.id ?? 'cta',
        });
      },
      { signal: ctaAbort.signal },
    );
  }

  // ---- video play / complete ----
  const videoAbort = new AbortController();
  if (opts.videoEl) {
    opts.videoEl.addEventListener(
      'play',
      () => sendOnce('portal.video_play'),
      { signal: videoAbort.signal },
    );
    opts.videoEl.addEventListener(
      'ended',
      () => sendOnce('portal.video_complete'),
      { signal: videoAbort.signal },
    );
    // Count "≥95% watched" as a complete too — hero videos often loop
    // before the ended event fires, so intended completes get lost.
    opts.videoEl.addEventListener(
      'timeupdate',
      () => {
        const v = opts.videoEl!;
        if (v.duration > 0 && v.currentTime / v.duration >= 0.95) {
          sendOnce('portal.video_complete', { via: 'timeupdate' });
        }
      },
      { signal: videoAbort.signal },
    );
  }

  // ---- heartbeat (every 15s while tab is visible) ----
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  const startHeartbeat = () => {
    if (heartbeatTimer) return;
    heartbeatTimer = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      send('portal.heartbeat');
    }, heartbeatMs);
  };
  const stopHeartbeat = () => {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  };
  startHeartbeat();

  const onVisibility = () => {
    // Hidden tabs stop burning heartbeats — we don't want to score a
    // lead who left the tab open in the background.
    if (document.hidden) {
      stopHeartbeat();
    } else {
      startHeartbeat();
    }
  };
  document.addEventListener('visibilitychange', onVisibility);

  // ---- unload ----
  const onLeave = () => {
    send('portal.leave');
  };
  window.addEventListener('pagehide', onLeave);

  return {
    sessionId,
    track: send,
    stop: () => {
      window.removeEventListener('scroll', onScroll);
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pagehide', onLeave);
      roiObserver?.disconnect();
      ctaAbort.abort();
      videoAbort.abort();
      stopHeartbeat();
    },
  };
}
