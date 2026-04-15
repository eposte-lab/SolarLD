import { AbsoluteFill, Img, interpolate, useCurrentFrame } from 'remotion';
import { z } from 'zod';

export const solarTransitionSchema = z.object({
  beforeImageUrl: z.string().url(),
  afterImageUrl: z.string().url(),
  kwp: z.number(),
  yearlySavingsEur: z.number(),
  paybackYears: z.number(),
  tenantName: z.string(),
});

export type SolarTransitionProps = z.infer<typeof solarTransitionSchema>;

export const SolarTransition: React.FC<SolarTransitionProps> = ({
  beforeImageUrl,
  afterImageUrl,
  kwp,
  yearlySavingsEur,
  paybackYears,
  tenantName,
}) => {
  const frame = useCurrentFrame();

  // 0-60: solo before
  // 60-120: crossfade before → after
  // 120-180: after + overlay text
  const afterOpacity = interpolate(frame, [60, 120], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const textOpacity = interpolate(frame, [120, 150], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill style={{ background: '#0f172a' }}>
      <AbsoluteFill>
        <Img src={beforeImageUrl} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
      </AbsoluteFill>
      <AbsoluteFill style={{ opacity: afterOpacity }}>
        <Img src={afterImageUrl} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
      </AbsoluteFill>

      <AbsoluteFill
        style={{
          opacity: textOpacity,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'flex-end',
          padding: 48,
          color: 'white',
          background: 'linear-gradient(to top, rgba(0,0,0,0.75), rgba(0,0,0,0))',
        }}
      >
        <div
          style={{
            fontSize: 48,
            fontWeight: 700,
            textShadow: '0 2px 4px rgba(0,0,0,0.5)',
          }}
        >
          {kwp.toFixed(1)} kWp installabili
        </div>
        <div style={{ fontSize: 28, marginTop: 8 }}>
          Risparmio annuo: € {yearlySavingsEur.toLocaleString('it-IT')}
        </div>
        <div style={{ fontSize: 24, marginTop: 4, opacity: 0.85 }}>
          Payback ~ {paybackYears.toFixed(1)} anni
        </div>
        <div style={{ fontSize: 16, marginTop: 24, opacity: 0.7 }}>{tenantName}</div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
