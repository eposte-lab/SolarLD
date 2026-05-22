'use client';

/**
 * EditorialHero — full-bleed hero with the rendered MP4 + audio toggle.
 *
 * Sprint 8 Fase A.3. The video autoplays muted/looped (Gmail/Outlook
 * email clients can't show MP4, so this hero is *the* moment the lead
 * sees their roof in motion). On unmute or fullscreen the user signals
 * high intent — we capture both as ``portal.audio_on`` and
 * ``portal.video_fullscreen`` portal events, which Fase C.1 weights as
 * +8 each on engagement_score.
 *
 * Falls back to the GIF, then the static image, then a placeholder.
 */

import { useRef, useState } from 'react';

import { postPortalEvent } from '@/lib/tracking';

type Props = {
  slug: string;
  videoUrl: string | null;
  gifUrl: string | null;
  imageUrl: string | null;
  brandColor: string;
  posterUrl?: string | null;
};

export function EditorialHero({
  slug,
  videoUrl,
  gifUrl,
  imageUrl,
  brandColor,
  posterUrl,
}: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [muted, setMuted] = useState(true);

  const handleAudioToggle = () => {
    const v = videoRef.current;
    if (!v) return;
    const next = !muted;
    v.muted = next;
    setMuted(next);
    if (!next) {
      // Unmute = high intent. Track once per page (sendBeacon on the
      // backend will dedupe further at session/min granularity).
      postPortalEvent(slug, 'portal.audio_on');
      // Some browsers pause when un-muting an autoplay video.
      v.play().catch(() => undefined);
    }
  };

  const handleFullscreen = async () => {
    const v = videoRef.current;
    if (!v) return;
    try {
      // requestFullscreen returns a Promise on modern browsers.
      const req =
        v.requestFullscreen ||
        // @ts-expect-error vendor prefix on older WebKit
        v.webkitRequestFullscreen ||
        // @ts-expect-error vendor prefix on Edge legacy
        v.msRequestFullscreen;
      if (req) {
        await req.call(v);
        postPortalEvent(slug, 'portal.video_fullscreen');
      }
    } catch {
      // no-op — user can still tap-to-fullscreen the native controls.
    }
  };

  if (videoUrl) {
    return (
      <div
        className="relative overflow-hidden rounded-3xl shadow-ambient-lg"
        style={{ aspectRatio: '16 / 9' }}
      >
        <video
          ref={videoRef}
          src={videoUrl}
          poster={posterUrl ?? undefined}
          autoPlay
          muted={muted}
          loop
          playsInline
          className="h-full w-full object-cover"
          data-portal-video
        />
        {/* Subtle vignette for legibility of the controls. */}
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/35 via-transparent to-black/10" />
        {/* Brand band along bottom edge. */}
        <div
          className="pointer-events-none absolute inset-x-0 bottom-0 h-1.5"
          style={{ backgroundColor: brandColor }}
        />
        {/* Controls overlay */}
        <div className="absolute bottom-4 right-4 flex items-center gap-2">
          <button
            type="button"
            onClick={handleAudioToggle}
            className="inline-flex items-center gap-2 rounded-full bg-black/55 px-4 py-2 text-xs font-semibold text-white backdrop-blur transition-colors hover:bg-black/75"
            aria-label={muted ? 'Attiva audio' : 'Silenzia'}
          >
            <span aria-hidden>{muted ? '🔇' : '🔊'}</span>
            <span>{muted ? 'Audio off' : 'Audio on'}</span>
          </button>
          <button
            type="button"
            onClick={handleFullscreen}
            className="inline-flex items-center gap-2 rounded-full bg-black/55 px-4 py-2 text-xs font-semibold text-white backdrop-blur transition-colors hover:bg-black/75"
            aria-label="Schermo intero"
          >
            <span aria-hidden>⛶</span>
            <span>Full screen</span>
          </button>
        </div>
      </div>
    );
  }

  // Fallbacks — GIF, then static image, then placeholder.
  if (gifUrl) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={gifUrl}
        alt="Rendering animato del tetto"
        className="w-full rounded-3xl shadow-ambient-lg"
      />
    );
  }
  if (imageUrl) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={imageUrl}
        alt="Rendering del tetto con fotovoltaico"
        className="w-full rounded-3xl shadow-ambient-lg"
      />
    );
  }
  return (
    <div className="flex aspect-video items-center justify-center rounded-3xl bg-surface-container-high text-on-surface-muted">
      Rendering in preparazione
    </div>
  );
}
