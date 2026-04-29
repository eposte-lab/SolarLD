'use client';

/**
 * IdleLogout — fires `/signout` after a configurable window of user
 * inactivity. Mounted once in the dashboard layout so every authenticated
 * route is covered.
 *
 * Design choices:
 *
 * - We listen on a small set of "real interaction" events. Bare scroll
 *   without input is intentionally NOT a reset signal: a user who left
 *   their laptop on a long page shouldn't keep the session alive just
 *   because the trackpad bumped.
 * - Events are passive + bound to `document` so React's tree doesn't
 *   need to plumb refs through every page.
 * - Timer is debounced — every interaction resets a single setTimeout
 *   instead of tracking timestamps. Cheap and good enough.
 * - On timeout we redirect to `/signout` (which clears device cookie +
 *   Supabase session + bounces to /login). We use a hard navigation
 *   `window.location.assign` instead of `router.push` because the
 *   route handler returns a redirect that the SPA router would not
 *   follow as expected.
 * - When the tenant has no idle timeout configured (or the gate is
 *   disabled) we render nothing — zero overhead path.
 */

import { useEffect, useRef } from 'react';

interface IdleLogoutProps {
  /** Minutes of inactivity before forced sign-out. Falsy → disabled. */
  idleMinutes: number | null | undefined;
}

const ACTIVITY_EVENTS: Array<keyof DocumentEventMap> = [
  'mousedown',
  'mousemove',
  'keydown',
  'touchstart',
  'click',
];

export function IdleLogout({ idleMinutes }: IdleLogoutProps) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!idleMinutes || idleMinutes <= 0) return;

    const timeoutMs = idleMinutes * 60 * 1000;

    const signOut = () => {
      // Hard navigation so the server redirect is honored end-to-end.
      window.location.assign('/signout');
    };

    const resetTimer = () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(signOut, timeoutMs);
    };

    // Pause the countdown when the tab is hidden — coming back gives
    // the user a fresh window. This avoids logging people out simply
    // because they switched to another tab to copy text.
    const onVisibility = () => {
      if (document.visibilityState === 'visible') {
        resetTimer();
      } else if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };

    ACTIVITY_EVENTS.forEach((evt) => {
      document.addEventListener(evt, resetTimer, { passive: true });
    });
    document.addEventListener('visibilitychange', onVisibility);

    resetTimer();

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      ACTIVITY_EVENTS.forEach((evt) => {
        document.removeEventListener(evt, resetTimer);
      });
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [idleMinutes]);

  return null;
}
