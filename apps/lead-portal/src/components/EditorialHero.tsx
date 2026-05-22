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

import { useEffect, useRef, useState } from 'react';

import { postPortalEvent } from '@/lib/tracking';

/** Quando la prima riproduzione della transition finisce (il crossfade
 *  before→after ha raggiunto il frame finale, di solito 3-4 s), attiviamo
 *  un'aura luminosa pulsante in brand color attorno al video — dà l'idea
 *  di "impianto acceso / efficientamento attivato". Loop continuo da
 *  lì in poi, finché l'utente non scrolla altrove. */
const FIRST_CYCLE_FALLBACK_MS = 4000;

/** Converte "#RRGGBB" in "r, g, b". Permette di costruire `rgba(...)`
 *  con qualsiasi alpha senza dipendere da `color-mix` (non supportato
 *  in alcune WebView aziendali). */
function hexToRgbChannels(hex: string): string {
  const h = (hex || '').replace('#', '').trim();
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  if (full.length !== 6 || /[^0-9a-fA-F]/.test(full)) return '24, 48, 84';
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  return `${r}, ${g}, ${b}`;
}

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
  const [auraOn, setAuraOn] = useState(false);
  const auraTimerRef = useRef<number | null>(null);
  const auraRgb = hexToRgbChannels(brandColor);

  // Fallback unconditional: 5 s dopo il mount accendiamo l'aura comunque,
  // anche se `onLoadedMetadata` non scatta (alcuni browser interni di
  // mail-client non firano l'evento in modo affidabile). `setAuraOn` è
  // idempotente: se l'evento è già scattato prima, questo è un no-op.
  useEffect(() => {
    const t = window.setTimeout(() => setAuraOn(true), 5000);
    return () => window.clearTimeout(t);
  }, []);

  const scheduleAura = (ms: number) => {
    if (auraTimerRef.current !== null) {
      window.clearTimeout(auraTimerRef.current);
    }
    auraTimerRef.current = window.setTimeout(() => setAuraOn(true), ms);
  };

  const handleLoadedMetadata = (e: React.SyntheticEvent<HTMLVideoElement>) => {
    const dur = e.currentTarget.duration;
    const ms =
      Number.isFinite(dur) && dur > 0
        ? Math.round(dur * 1000) + 100
        : FIRST_CYCLE_FALLBACK_MS;
    scheduleAura(ms);
  };

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
        className="relative rounded-3xl"
        style={
          {
            aspectRatio: '16 / 9',
            // Canali RGB del brand color iniettati come custom property
            // così i keyframe possono costruire rgba() con alpha
            // variabile senza dipendere da `color-mix` (non supportato
            // ovunque). L'`animation` è sempre attiva: l'aura più
            // intensa dopo il primo ciclo del video, soft prima — vedi
            // i keyframe `ehAuraPulse*` sotto. Niente `overflow-hidden`
            // su questo wrapper esterno: certi engine (WebKit con
            // border-radius + overflow-hidden) chiudono il paint nello
            // stacking context dell'elemento e la box-shadow non esce.
            '--ehAuraRgb': auraRgb,
            animation: auraOn
              ? 'ehAuraPulseHigh 2.4s ease-in-out infinite'
              : 'ehAuraPulseSoft 3.6s ease-in-out infinite',
          } as React.CSSProperties
        }
      >
        {/* Keyframes — due livelli: "soft" prima del primo ciclo del
            video, "high" dopo. Definiti inline così niente cambia in
            globals.css. */}
        <style>
          {`
            @keyframes ehAuraPulseSoft {
              0%, 100% {
                box-shadow:
                  0 0 24px 4px rgba(var(--ehAuraRgb), 0.35),
                  0 0 0 1px rgba(var(--ehAuraRgb), 0.20);
              }
              50% {
                box-shadow:
                  0 0 48px 10px rgba(var(--ehAuraRgb), 0.55),
                  0 0 0 2px rgba(var(--ehAuraRgb), 0.40);
              }
            }
            @keyframes ehAuraPulseHigh {
              0%, 100% {
                box-shadow:
                  0 0 40px 8px rgba(var(--ehAuraRgb), 0.65),
                  0 0 0 2px rgba(var(--ehAuraRgb), 0.50);
              }
              50% {
                box-shadow:
                  0 0 110px 28px rgba(var(--ehAuraRgb), 0.95),
                  0 0 0 4px rgba(var(--ehAuraRgb), 0.80);
              }
            }
          `}
        </style>
        {/* Wrapper INTERNO che si occupa di clippare il video sugli
            angoli arrotondati. L'overflow-hidden vive QUI così non
            tocca lo stacking-context del box-shadow del wrapper esterno. */}
        <div
          className="absolute inset-0 overflow-hidden rounded-3xl shadow-ambient-lg"
        >
          <video
            ref={videoRef}
            src={videoUrl}
            poster={posterUrl ?? undefined}
            autoPlay
            muted={muted}
            loop
            playsInline
            onLoadedMetadata={handleLoadedMetadata}
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
