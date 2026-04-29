/**
 * Next.js dynamic favicon — renders the real SolarLead logo PNG.
 *
 * The static asset at /logo.png is fetched and returned as-is.
 * If the asset is missing during build (e.g. CI before the file is
 * committed), we fall back to a minimal SVG glyph so the build
 * doesn't fail.
 */

import { ImageResponse } from 'next/og';

export const size = { width: 32, height: 32 };
export const contentType = 'image/png';

export default async function Icon() {
  const base =
    process.env.NEXT_PUBLIC_VERCEL_URL
      ? `https://${process.env.NEXT_PUBLIC_VERCEL_URL}`
      : process.env.NEXT_PUBLIC_DASHBOARD_URL ?? 'http://localhost:3000';

  let logoData: string | null = null;
  try {
    const res = await fetch(`${base}/logo.png`);
    if (res.ok) {
      const buf = await res.arrayBuffer();
      logoData = `data:image/png;base64,${Buffer.from(buf).toString('base64')}`;
    }
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

  // Fallback glyph — identical to the old SVG mark so CI doesn't break
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
