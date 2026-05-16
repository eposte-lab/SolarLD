'use client';

/**
 * Wires the engagement beacon into the lead-portal page.
 *
 * Sits next to ``VisitTracker`` (which handles the one-shot business
 * event ``lead.portal_visited``) and handles the high-cardinality
 * telemetry stream: scroll milestones, ROI section view, CTA hovers,
 * video play/complete, 15s heartbeats, and pagehide.
 *
 * Why it's a separate component (not folded into VisitTracker):
 *   - VisitTracker fires once per mount and is done. PortalTracker
 *     holds live listeners for the life of the page.
 *   - VisitTracker hits ``/v1/public/lead/{slug}/visit`` (legacy
 *     business-event route). PortalTracker hits
 *     ``/v1/public/portal/track``. Different semantics, different
 *     rate-limits, different tables.
 *
 * The component is zero-DOM — it uses ``data-*`` selectors to find the
 * elements it observes, so the page JSX doesn't need to pass refs.
 * The contract is:
 *
 *   * ``[data-portal-roi]``    — ROI stats card (for ``portal.roi_viewed``)
 *   * ``[data-portal-video]``  — hero video element
 *   * ``[data-portal-cta]``    — any CTA button/card (WhatsApp, appointment)
 *
 * If the selectors don't match anything the tracker still runs — it
 * just won't emit those specific events.
 */

import { useEffect } from 'react';
import { startPortalTracker } from '@/lib/tracking';

export function PortalTracker({ slug }: { slug: string }) {
  useEffect(() => {
    const roiSectionEl =
      document.querySelector<HTMLElement>('[data-portal-roi]');
    const videoEl =
      document.querySelector<HTMLVideoElement>('[data-portal-video]');
    const ctaEls = Array.from(
      document.querySelectorAll<HTMLElement>('[data-portal-cta]'),
    );

    const handle = startPortalTracker(slug, {
      roiSectionEl,
      videoEl,
      ctaEls,
    });
    return () => handle.stop();
  }, [slug]);

  return null;
}
