/**
 * Next.js dynamic favicon — serves the real SolarLead logo PNG.
 *
 * The previous implementation did an HTTP fetch of `/logo.png` from
 * `NEXT_PUBLIC_VERCEL_URL` at build time, which 404s during the very
 * deployment that's producing the favicon (the URL isn't live yet)
 * and silently falls back to the OLD SVG glyph — that's why users
 * keep seeing the legacy mark in the browser tab.
 *
 * Switch to a filesystem read at request time. Next.js inlines this
 * route as a dynamic asset, so each request streams the bytes from
 * `public/logo.png` directly. No env vars, no chicken-and-egg fetch.
 */

import { promises as fs } from 'node:fs';
import path from 'node:path';

import { ImageResponse } from 'next/og';

export const size = { width: 32, height: 32 };
export const contentType = 'image/png';

export default async function Icon() {
  let logoData: string | null = null;
  try {
    const logoPath = path.join(process.cwd(), 'public', 'logo.png');
    const buf = await fs.readFile(logoPath);
    logoData = `data:image/png;base64,${buf.toString('base64')}`;
  } catch {
    // fall through to SVG fallback
  }

  if (logoData) {
    return new ImageResponse(
      (
        <div
          style={{
            width: '100%',
            height: '100%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: '#ffffff',
            borderRadius: '6px',
          }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={logoData} width={28} height={28} alt="" />
        </div>
      ),
      { ...size },
    );
  }

  // Fallback glyph — kept only as a last-resort so a missing PNG
  // never breaks the build. In practice the fs.readFile above always
  // succeeds because `public/logo.png` is committed.
  const MINT = '#6FCF97';
  const SURFACE = '#0A0B0C';
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: SURFACE,
          borderRadius: '7px',
        }}
      >
        <svg width="28" height="28" viewBox="0 0 28 28" xmlns="http://www.w3.org/2000/svg">
          <rect x="3" y="3" width="22" height="22" rx="6.25" fill="none" stroke={MINT} strokeWidth="1.6" />
          <line x1="14" y1="5" x2="14" y2="23" stroke={MINT} strokeWidth="1" strokeOpacity="0.32" strokeLinecap="round" />
          <line x1="5" y1="14" x2="23" y2="14" stroke={MINT} strokeWidth="1" strokeOpacity="0.32" strokeLinecap="round" />
          <rect x="6.5" y="6.5" width="6" height="6" rx="1.6" fill={MINT} />
          <circle cx="19.5" cy="8.5" r="1.65" fill={MINT} />
          <circle cx="19.5" cy="8.5" r="3.2" fill="none" stroke={MINT} strokeWidth="0.9" strokeOpacity="0.35" />
        </svg>
      </div>
    ),
    { ...size },
  );
}
