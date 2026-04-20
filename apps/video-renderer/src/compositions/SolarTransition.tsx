import { AbsoluteFill, Img, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { z } from 'zod';

/**
 * SolarTransition — before → crossfade → after with ROI outro.
 *
 * Timing (30 fps, 180 frames = 6 seconds):
 *   0–60:   pure before shot (2 s)
 *   60–120: crossfade before → after (2 s)
 *   120–180: after + ROI overlay + installer branding (2 s)
 *
 * The outro panel uses `brandPrimaryColor` (the installer's Tailwind-ish
 * hex) as the accent line + KPI color so the video feels like part of
 * the installer's own content. The logo sits bottom-right so it never
 * overlaps the ROI text.
 */
export const solarTransitionSchema = z.object({
  beforeImageUrl: z.string().url(),
  afterImageUrl: z.string().url(),
  kwp: z.number(),
  yearlySavingsEur: z.number(),
  paybackYears: z.number(),
  co2TonnesLifetime: z.number().optional(),
  tenantName: z.string(),
  brandPrimaryColor: z.string().regex(/^#[0-9a-fA-F]{3,8}$/).default('#0F766E'),
  brandLogoUrl: z.string().url().optional(),
});

export type SolarTransitionProps = z.infer<typeof solarTransitionSchema>;

export const SolarTransition: React.FC<SolarTransitionProps> = ({
  beforeImageUrl,
  afterImageUrl,
  kwp,
  yearlySavingsEur,
  paybackYears,
  co2TonnesLifetime,
  tenantName,
  brandPrimaryColor,
  brandLogoUrl,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  // Opacity ramps
  const afterOpacity = interpolate(frame, [60, 120], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const overlayOpacity = interpolate(frame, [120, 150], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  // Subtle zoom on the "after" so the outro feels alive
  const afterScale = interpolate(frame, [60, durationInFrames], [1.0, 1.06], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const formattedSavings = Math.round(yearlySavingsEur).toLocaleString('it-IT');

  return (
    <AbsoluteFill style={{ background: '#0f172a' }}>
      {/* BEFORE — always underneath */}
      <AbsoluteFill>
        <Img
          src={beforeImageUrl}
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
        />
      </AbsoluteFill>

      {/* AFTER — fades in 60→120 */}
      <AbsoluteFill style={{ opacity: afterOpacity }}>
        <Img
          src={afterImageUrl}
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            transform: `scale(${afterScale})`,
          }}
        />
      </AbsoluteFill>

      {/* Brand accent bar (always visible, but more prominent once
          we're on the after shot). */}
      <AbsoluteFill
        style={{
          pointerEvents: 'none',
          justifyContent: 'flex-end',
          alignItems: 'stretch',
          display: 'flex',
        }}
      >
        <div
          style={{
            height: 8,
            width: '100%',
            background: brandPrimaryColor,
            opacity: afterOpacity,
          }}
        />
      </AbsoluteFill>

      {/* ROI outro — fades in after the crossfade */}
      <AbsoluteFill
        style={{
          opacity: overlayOpacity,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-start',
          justifyContent: 'flex-end',
          padding: 56,
          color: 'white',
          background: 'linear-gradient(to top, rgba(0,0,0,0.80), rgba(0,0,0,0) 55%)',
        }}
      >
        <div
          style={{
            fontSize: 56,
            fontWeight: 700,
            lineHeight: 1.05,
            textShadow: '0 2px 6px rgba(0,0,0,0.55)',
            color: brandPrimaryColor,
          }}
        >
          {kwp.toFixed(1)} kWp
        </div>
        <div style={{ fontSize: 30, marginTop: 10, textShadow: '0 1px 3px rgba(0,0,0,0.6)' }}>
          € {formattedSavings} risparmio annuo
        </div>
        <div style={{ fontSize: 22, marginTop: 4, opacity: 0.85 }}>
          Rientro stimato ~ {paybackYears.toFixed(1)} anni
        </div>
        {co2TonnesLifetime !== undefined ? (
          <div style={{ fontSize: 18, marginTop: 4, opacity: 0.75 }}>
            ~ {co2TonnesLifetime.toFixed(0)} t CO₂ evitate in 25 anni
          </div>
        ) : null}
        <div style={{ fontSize: 15, marginTop: 28, opacity: 0.7, letterSpacing: 0.6 }}>
          Stima indicativa — preventivo formale a cura di {tenantName}
        </div>
      </AbsoluteFill>

      {/* Brand logo — bottom-right corner */}
      {brandLogoUrl ? (
        <AbsoluteFill
          style={{
            display: 'flex',
            alignItems: 'flex-end',
            justifyContent: 'flex-end',
            padding: 40,
            pointerEvents: 'none',
            opacity: overlayOpacity,
          }}
        >
          <Img
            src={brandLogoUrl}
            style={{
              maxWidth: 220,
              maxHeight: 80,
              objectFit: 'contain',
              filter: 'drop-shadow(0 2px 6px rgba(0,0,0,0.6))',
            }}
          />
        </AbsoluteFill>
      ) : null}
    </AbsoluteFill>
  );
};
