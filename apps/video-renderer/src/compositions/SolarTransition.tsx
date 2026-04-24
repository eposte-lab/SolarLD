import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import { z } from 'zod';

/**
 * SolarTransition — cinematic before → after for solar proposals.
 *
 * Timeline (30 fps, 180 frames = 6 seconds):
 *   000–054 (1.8 s)  Ken Burns on BEFORE — slow zoom-in + gentle pan.
 *   054–108 (1.8 s)  Diagonal wipe reveals AFTER on top of BEFORE;
 *                    both keep drifting so the camera never stalls.
 *   108–180 (2.4 s)  AFTER holds, continues Ken Burns; KPI stats
 *                    spring-in stagger (kWp → savings → payback → CO₂);
 *                    vignette + brand bar settle at the end.
 *
 * Design goals:
 *   · the camera is ALWAYS moving (no static frame) — feels alive
 *     even when rendered to GIF at 15 fps.
 *   · the wipe is diagonal and soft (gradient-masked) so the panel
 *     reveal reads as "installation sweeps across the roof" instead
 *     of "crossfade".
 *   · stats slide up one at a time with spring physics → reads as
 *     a proposal unfolding, not a slideshow ending.
 */
/**
 * Optional 3D scene payload.  When present the composition switches to
 * the cinematic Three.js renderer (SolarTransition3D) — camera orbit
 * around a 3D roof with lit panels as real meshes.  When absent the
 * composition falls back to the classic 2D Ken-Burns + wipe path
 * (SolarTransition2D) so old clients keep working unchanged.
 *
 * All positions in WGS84 lat/lng; the renderer projects them to metres
 * using the equirectangular approximation centred on ``centerLat``.
 */
export const scene3dSchema = z.object({
  aerialUrl: z.string().url(),           // flat before-aerial PNG (ground texture)
  centerLat: z.number(),
  centerLng: z.number(),
  radiusM: z.number(),                   // half-width of the aerial footprint
  panels: z.array(
    z.object({
      lat: z.number(),
      lng: z.number(),
      azimuthDeg: z.number(),            // panel-facing direction (0=N, 90=E)
      orientation: z.enum(['LANDSCAPE', 'PORTRAIT']).default('LANDSCAPE'),
    }),
  ),
  panelWidthM: z.number().default(1.045),
  panelHeightM: z.number().default(1.879),
  roofHeightM: z.number().default(7.0),  // best-guess building ridge height
});

export type Scene3d = z.infer<typeof scene3dSchema>;

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
  scene3d: scene3dSchema.optional(),
});

export type SolarTransitionProps = z.infer<typeof solarTransitionSchema>;

// Dynamic import so clients that don't pass scene3d don't pay the
// Three.js bundle weight (it's ~600 KB gzipped).  Since Remotion bundles
// everything statically we accept the cost, but keeping the imports
// split makes it trivial to lazy-load later if we want to.
import { SolarTransition3D } from './SolarTransition3D';

// ── Timing constants (30 fps baseline) ─────────────────────────────────────
const KB_END = 108;          // Ken Burns keeps zooming from 0 → 108
const WIPE_START = 54;
const WIPE_END = 108;
const STATS_START = 110;
const STATS_STAGGER = 10;    // frames between each stat line appearing

/**
 * Ken-Burns transform for a single layer.
 * Returns `scale` and `translate` values that drift slowly over time so
 * the image never sits still.  Different ``seed`` values produce slightly
 * different pan directions for before vs. after so the camera doesn't
 * appear to "glue" across the cut.
 */
function kenBurns(frame: number, seed: 'before' | 'after') {
  // Scale: 1.02 → 1.14 over the whole clip (never static).
  const scale = interpolate(frame, [0, 180], [1.02, 1.14], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  // Pan: a few percent across the frame, different direction per seed.
  const panX = interpolate(
    frame,
    [0, 180],
    seed === 'before' ? [-1.5, 1.5] : [1.2, -1.8],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
  );
  const panY = interpolate(
    frame,
    [0, 180],
    seed === 'before' ? [1.0, -1.5] : [-0.8, 1.2],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
  );
  return { scale, panX, panY };
}

/**
 * Diagonal wipe clip-path.
 *
 * Progress 0 → 1 sweeps a wedge from top-left to bottom-right; the
 * trailing edge is offset by 18% so the reveal line is clearly diagonal
 * instead of a flat vertical bar.  At progress 0 the polygon has zero
 * area (after is invisible); at 1 the polygon covers the whole frame.
 */
function diagonalWipe(progress: number): string {
  const leading = progress * 118;  // top edge x %, overshoots past 100
  const trailing = progress * 118 - 18; // bottom edge x %
  return `polygon(0% 0%, ${leading}% 0%, ${trailing}% 100%, 0% 100%)`;
}

export const SolarTransition: React.FC<SolarTransitionProps> = (props) => {
  // When the caller includes a 3D scene payload, render the cinematic
  // Three.js path (camera orbit + real panels with physical materials).
  // Otherwise fall back to the existing Ken-Burns + diagonal wipe path.
  if (props.scene3d) {
    return <SolarTransition3D {...props} scene3d={props.scene3d} />;
  }
  return <SolarTransition2D {...props} />;
};

const SolarTransition2D: React.FC<SolarTransitionProps> = ({
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
  const { fps } = useVideoConfig();

  // ── Ken-Burns transforms ────────────────────────────────────────────────
  const before = kenBurns(frame, 'before');
  const after = kenBurns(frame, 'after');

  // ── Wipe progress (054 → 108) ───────────────────────────────────────────
  const wipeProgress = interpolate(frame, [WIPE_START, WIPE_END], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const wipeClipPath = diagonalWipe(wipeProgress);

  // ── Wipe "edge glow" — a bright diagonal line that travels with the cut ─
  const edgeOpacity = interpolate(
    frame,
    [WIPE_START, WIPE_START + 6, WIPE_END - 6, WIPE_END + 4],
    [0, 0.9, 0.9, 0],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
  );
  const edgeX = interpolate(frame, [WIPE_START, WIPE_END], [-10, 110], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // ── Stats spring-in (staggered) ────────────────────────────────────────
  function statSpring(delay: number) {
    const local = frame - (STATS_START + delay);
    if (local <= 0) return { opacity: 0, translate: 24 };
    const p = spring({
      frame: local,
      fps,
      config: { damping: 14, stiffness: 80, mass: 0.9 },
    });
    return {
      opacity: p,
      translate: interpolate(p, [0, 1], [24, 0]),
    };
  }
  const sKwp = statSpring(0);
  const sSav = statSpring(STATS_STAGGER);
  const sPay = statSpring(STATS_STAGGER * 2);
  const sCo2 = statSpring(STATS_STAGGER * 3);

  // ── Vignette + brand bar opacity (settle toward the end) ───────────────
  const outroOpacity = interpolate(frame, [STATS_START - 4, STATS_START + 20], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const formattedSavings = Math.round(yearlySavingsEur).toLocaleString('it-IT');

  // Helper: render an image with Ken-Burns transform applied at the origin.
  const imageStyle = (kb: { scale: number; panX: number; panY: number }) => ({
    width: '100%',
    height: '100%',
    objectFit: 'cover' as const,
    transformOrigin: '50% 50%',
    transform: `translate(${kb.panX}%, ${kb.panY}%) scale(${kb.scale})`,
    willChange: 'transform',
  });

  return (
    <AbsoluteFill style={{ background: '#0b1120' }}>
      {/* BEFORE — always underneath, Ken Burns drifting continuously */}
      <AbsoluteFill>
        <Img src={beforeImageUrl} style={imageStyle(before)} />
      </AbsoluteFill>

      {/* AFTER — clipped by the diagonal wipe; own Ken Burns direction.
          Once the wipe finishes (frame >= 108) the clip-path is full
          coverage so AFTER hides BEFORE completely for the outro. */}
      <AbsoluteFill style={{ clipPath: wipeClipPath }}>
        <Img src={afterImageUrl} style={imageStyle(after)} />
      </AbsoluteFill>

      {/* Edge glow — bright diagonal strip tracking the wipe line.
          Skewed with the same 18% offset (≈ 10° slant) so it follows
          the clip-path edge instead of being a vertical bar. */}
      <AbsoluteFill style={{ pointerEvents: 'none', opacity: edgeOpacity }}>
        <div
          style={{
            position: 'absolute',
            top: '-10%',
            height: '120%',
            width: '10%',
            left: `${edgeX}%`,
            transform: 'skewX(-10deg)',
            background:
              'linear-gradient(90deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.55) 45%, rgba(255,255,255,0.95) 50%, rgba(255,255,255,0.55) 55%, rgba(255,255,255,0) 100%)',
            filter: 'blur(1px)',
          }}
        />
      </AbsoluteFill>

      {/* Bottom vignette — settles in for the outro so stats stay readable */}
      <AbsoluteFill
        style={{
          pointerEvents: 'none',
          opacity: outroOpacity,
          background:
            'linear-gradient(to top, rgba(0,0,0,0.82) 0%, rgba(0,0,0,0.55) 25%, rgba(0,0,0,0) 60%)',
        }}
      />

      {/* Brand accent bar at the very bottom — tracks outroOpacity */}
      <AbsoluteFill
        style={{
          pointerEvents: 'none',
          justifyContent: 'flex-end',
          display: 'flex',
          opacity: outroOpacity,
        }}
      >
        <div
          style={{
            height: 6,
            width: '100%',
            background: brandPrimaryColor,
            boxShadow: `0 0 12px ${brandPrimaryColor}`,
          }}
        />
      </AbsoluteFill>

      {/* ROI outro — each line spring-staggered */}
      <AbsoluteFill
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-start',
          justifyContent: 'flex-end',
          padding: 56,
          color: 'white',
        }}
      >
        <div
          style={{
            fontSize: 64,
            fontWeight: 700,
            lineHeight: 1.02,
            letterSpacing: -1,
            textShadow: '0 2px 8px rgba(0,0,0,0.55)',
            color: brandPrimaryColor,
            opacity: sKwp.opacity,
            transform: `translateY(${sKwp.translate}px)`,
          }}
        >
          {kwp.toFixed(1)} kWp
        </div>
        <div
          style={{
            fontSize: 32,
            marginTop: 12,
            fontWeight: 600,
            textShadow: '0 1px 4px rgba(0,0,0,0.65)',
            opacity: sSav.opacity,
            transform: `translateY(${sSav.translate}px)`,
          }}
        >
          € {formattedSavings} risparmio annuo
        </div>
        <div
          style={{
            fontSize: 22,
            marginTop: 6,
            opacity: 0.85 * sPay.opacity,
            textShadow: '0 1px 3px rgba(0,0,0,0.6)',
            transform: `translateY(${sPay.translate}px)`,
          }}
        >
          Rientro stimato ~ {paybackYears.toFixed(1)} anni
        </div>
        {co2TonnesLifetime !== undefined ? (
          <div
            style={{
              fontSize: 18,
              marginTop: 4,
              opacity: 0.75 * sCo2.opacity,
              textShadow: '0 1px 3px rgba(0,0,0,0.6)',
              transform: `translateY(${sCo2.translate}px)`,
            }}
          >
            ~ {co2TonnesLifetime.toFixed(0)} t CO₂ evitate in 25 anni
          </div>
        ) : null}
        <div
          style={{
            fontSize: 15,
            marginTop: 28,
            opacity: 0.7 * outroOpacity,
            letterSpacing: 0.6,
          }}
        >
          Stima indicativa — preventivo formale a cura di {tenantName}
        </div>
      </AbsoluteFill>

      {/* Brand logo — bottom-right corner, fades with the outro */}
      {brandLogoUrl ? (
        <AbsoluteFill
          style={{
            display: 'flex',
            alignItems: 'flex-end',
            justifyContent: 'flex-end',
            padding: 40,
            pointerEvents: 'none',
            opacity: outroOpacity,
          }}
        >
          <Img
            src={brandLogoUrl}
            style={{
              maxWidth: 220,
              maxHeight: 80,
              objectFit: 'contain',
              filter: 'drop-shadow(0 2px 8px rgba(0,0,0,0.7))',
            }}
          />
        </AbsoluteFill>
      ) : null}
    </AbsoluteFill>
  );
};
