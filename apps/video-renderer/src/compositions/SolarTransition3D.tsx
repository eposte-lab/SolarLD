import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import { ThreeCanvas } from '@remotion/three';
import { useLoader, useThree } from '@react-three/fiber';
import { useEffect } from 'react';
import * as THREE from 'three';

import type { Scene3d, SolarTransitionProps } from './SolarTransition';

/**
 * SolarTransition3D — cinematic camera orbit around a lit 3D building.
 *
 * Geometry:
 *   · ground plane = 120 × 120 m, textured with the aerial satellite PNG
 *   · roof slab    = extruded polygon covering the panel footprint, lifted
 *                    to `roofHeightM`; single flat top (first iteration —
 *                    per-segment pitch will land in a follow-up)
 *   · each panel   = 1.05 × 0.04 × 1.88 m box with MeshPhysicalMaterial
 *                    (deep blue glass, clearcoat) + silver frame edge
 *
 * Timeline (180 frames @ 30 fps = 6 s):
 *   000–054  establish: camera 80 m high, dive toward the building
 *   054–108  orbit 90° around the building centroid while panels
 *            reveal one-by-one sorted east→west ("installation sweep")
 *   108–180  settle at a 3/4 low-angle hero shot, stats spring-in
 *
 * Coordinate system (right-handed, Three.js default):
 *   +x = east (lng increases)
 *   +z = south (lat decreases)
 *   +y = up
 *
 * Everything is in metres; `centerLat / centerLng` is the origin.
 */

type Props = SolarTransitionProps & { scene3d: Scene3d };

const STATS_START = 110;
const STATS_STAGGER = 10;

// ── Geo projection helpers ────────────────────────────────────────────────

const M_PER_DEG_LAT = 111_320;

function lngToX(lng: number, centerLat: number, centerLng: number): number {
  return (lng - centerLng) * M_PER_DEG_LAT * Math.cos((centerLat * Math.PI) / 180);
}

function latToZ(lat: number, centerLat: number): number {
  // +z is south → as lat decreases (go south) z increases.
  return (centerLat - lat) * M_PER_DEG_LAT;
}

// ── Camera path ────────────────────────────────────────────────────────────

/** Camera orbit described in spherical coords relative to the origin. */
function cameraPosition(frame: number): [number, number, number] {
  // Azimuth: start looking from NE, orbit around to SW (~110° sweep).
  const azRad =
    (interpolate(frame, [0, 54, 180], [45, 45, 155], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    }) *
      Math.PI) /
    180;
  // Elevation: start steep (near top-down) then dive to a 30° low-angle.
  const elRad =
    (interpolate(frame, [0, 54, 108, 180], [72, 60, 32, 28], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    }) *
      Math.PI) /
    180;
  // Distance: zoom in from 85 m to a tight 32 m hero framing.
  const dist = interpolate(frame, [0, 54, 108, 180], [85, 55, 38, 32], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const horiz = dist * Math.cos(elRad);
  return [
    horiz * Math.sin(azRad),
    dist * Math.sin(elRad),
    horiz * Math.cos(azRad),
  ];
}

// ── Texture loader — usable inside ThreeCanvas ────────────────────────────

function GroundPlane({ url, radiusM }: { url: string; radiusM: number }) {
  const texture = useLoader(THREE.TextureLoader, url);
  // The aerial is rendered so its pixels align with the geo extent:
  // side = 2 × radiusM.  colorSpace ensures correct gamma under lights.
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  const side = radiusM * 2;
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0, 0]} receiveShadow>
      <planeGeometry args={[side, side, 1, 1]} />
      <meshStandardMaterial map={texture} roughness={0.95} metalness={0.0} />
    </mesh>
  );
}

// ── Roof slab — a low, near-invisible platform that anchors the panels
//    above the aerial.  Real per-segment pitch lands in a follow-up.

function RoofSlab({
  scene,
  frame,
}: {
  scene: Scene3d;
  frame: number;
}) {
  if (scene.panels.length === 0) return null;

  // Compute bounding box of panels in metres, add a 1 m border.
  let minX = Infinity;
  let maxX = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;
  for (const p of scene.panels) {
    const px = lngToX(p.lng, scene.centerLat, scene.centerLng);
    const pz = latToZ(p.lat, scene.centerLat);
    if (px < minX) minX = px;
    if (px > maxX) maxX = px;
    if (pz < minZ) minZ = pz;
    if (pz > maxZ) maxZ = pz;
  }
  const pad = 1.2;
  const width = Math.max(maxX - minX, 6) + pad * 2;
  const depth = Math.max(maxZ - minZ, 6) + pad * 2;
  const cx = (minX + maxX) / 2;
  const cz = (minZ + maxZ) / 2;

  // Slab rises during the establishing shot so it doesn't pop in.
  const slabHeight = interpolate(frame, [0, 40], [0.1, scene.roofHeightM], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <mesh
      position={[cx, slabHeight / 2, cz]}
      castShadow
      receiveShadow
    >
      <boxGeometry args={[width, slabHeight, depth]} />
      <meshStandardMaterial
        color="#3a3f48"
        roughness={0.85}
        metalness={0.05}
      />
    </mesh>
  );
}

// ── Panel — a box with physical glass material + thin silver frame ─────────

function Panel({
  position,
  azimuthDeg,
  widthM,
  heightM,
  opacity,
}: {
  position: [number, number, number];
  azimuthDeg: number;
  widthM: number;
  heightM: number;
  opacity: number;
}) {
  // Panel local axis: long side along z, short along x.  LANDSCAPE
  // installations point the long side perpendicular to azimuth (the
  // sun direction); rotating by (azimuth + 90°) makes the short side
  // face the sun.  Azimuth 0 = N so we rotate around y by -azimuthRad.
  const rotY = -((azimuthDeg + 90) * Math.PI) / 180;
  const thickness = 0.04;
  return (
    <group position={position} rotation={[0, rotY, 0]}>
      {/* Glass body */}
      <mesh castShadow receiveShadow>
        <boxGeometry args={[widthM, thickness, heightM]} />
        <meshPhysicalMaterial
          color="#0c1a3a"
          metalness={0.25}
          roughness={0.18}
          clearcoat={0.9}
          clearcoatRoughness={0.08}
          transparent
          opacity={opacity}
          reflectivity={0.5}
        />
      </mesh>
      {/* Silver frame — drawn as a slightly larger, flatter box under the
          glass so the edges are visible from any orbit angle. */}
      <mesh position={[0, -thickness / 2 - 0.002, 0]}>
        <boxGeometry args={[widthM + 0.02, 0.01, heightM + 0.02]} />
        <meshStandardMaterial
          color="#c9ccd1"
          metalness={0.9}
          roughness={0.35}
          transparent
          opacity={opacity}
        />
      </mesh>
    </group>
  );
}

// ── Panels group with staggered east→west reveal ──────────────────────────

function PanelArray({
  scene,
  frame,
  fps,
}: {
  scene: Scene3d;
  frame: number;
  fps: number;
}) {
  // Sort panels by x (east→west) so the reveal sweeps in a single direction.
  const sorted = [...scene.panels]
    .map((p) => ({
      ...p,
      x: lngToX(p.lng, scene.centerLat, scene.centerLng),
      z: latToZ(p.lat, scene.centerLat),
    }))
    .sort((a, b) => b.x - a.x); // east first (largest x)

  const total = sorted.length || 1;
  const revealStart = 54;
  const revealEnd = 128;
  const perPanelFrames = (revealEnd - revealStart) / total;

  return (
    <group>
      {sorted.map((p, i) => {
        const delay = revealStart + i * perPanelFrames;
        const local = frame - delay;
        // Each panel springs in over ~15 frames.
        const progress =
          local <= 0
            ? 0
            : spring({
                frame: local,
                fps,
                config: { damping: 16, stiffness: 110, mass: 0.9 },
              });
        // During reveal panel drifts from 4m above resting height → snap down.
        const lift = (1 - progress) * 4;
        return (
          <Panel
            key={`p-${i}`}
            position={[p.x, scene.roofHeightM + 0.05 + lift, p.z]}
            azimuthDeg={p.azimuthDeg}
            widthM={scene.panelWidthM}
            heightM={scene.panelHeightM}
            opacity={progress}
          />
        );
      })}
    </group>
  );
}

// ── Scene lighting ────────────────────────────────────────────────────────

function Lighting() {
  return (
    <>
      <ambientLight intensity={0.45} />
      {/* Sun — Italian midday south-east, casts shadows on roof + ground */}
      <directionalLight
        position={[30, 50, -20]}
        intensity={1.8}
        castShadow
        shadow-mapSize-width={1024}
        shadow-mapSize-height={1024}
        shadow-camera-near={0.5}
        shadow-camera-far={200}
        shadow-camera-left={-40}
        shadow-camera-right={40}
        shadow-camera-top={40}
        shadow-camera-bottom={-40}
      />
      {/* Rim light — subtle blue fill from opposite side so panels don't
          look flat when the orbit brings them to the backlit side */}
      <directionalLight position={[-40, 30, 40]} intensity={0.35} color="#6f8fff" />
    </>
  );
}

// ── The 3D scene wrapper ───────────────────────────────────────────────────

/**
 * Drives the default camera every frame.  We avoid mounting a custom
 * camera (which would need `makeDefault` from drei) and instead read
 * R3F's built-in default camera via `useThree` and mutate it.  This
 * guarantees the camera stays orthogonal to animation state at render
 * time — Remotion re-evaluates hooks once per frame.
 */
function CameraController({ frame, focusY }: { frame: number; focusY: number }) {
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const [cx, cy, cz] = cameraPosition(frame);
  useEffect(() => {
    camera.position.set(cx, cy, cz);
    camera.fov = 38;
    camera.near = 0.5;
    camera.far = 500;
    camera.lookAt(0, focusY, 0);
    camera.updateProjectionMatrix();
  }, [camera, cx, cy, cz, focusY]);
  return null;
}

function Scene({ scene, frame, fps }: { scene: Scene3d; frame: number; fps: number }) {
  return (
    <>
      <CameraController frame={frame} focusY={scene.roofHeightM * 0.6} />
      <Lighting />
      <GroundPlane url={scene.aerialUrl} radiusM={scene.radiusM} />
      <RoofSlab scene={scene} frame={frame} />
      <PanelArray scene={scene} frame={frame} fps={fps} />
      {/* Soft fog so the far edges of the aerial fall into a dark haze
          instead of hard-cutting at the plane boundary */}
      <fog attach="fog" args={['#0b1120', 50, 160]} />
    </>
  );
}

// ── Stats overlay (2D HTML on top of the 3D canvas) ───────────────────────

function StatsOverlay({
  kwp,
  yearlySavingsEur,
  paybackYears,
  co2TonnesLifetime,
  tenantName,
  brandPrimaryColor,
  brandLogoUrl,
  frame,
  fps,
}: {
  kwp: number;
  yearlySavingsEur: number;
  paybackYears: number;
  co2TonnesLifetime?: number;
  tenantName: string;
  brandPrimaryColor: string;
  brandLogoUrl?: string;
  frame: number;
  fps: number;
}) {
  function statSpring(delay: number) {
    const local = frame - (STATS_START + delay);
    if (local <= 0) return { opacity: 0, translate: 24 };
    const p = spring({
      frame: local,
      fps,
      config: { damping: 14, stiffness: 80, mass: 0.9 },
    });
    return { opacity: p, translate: interpolate(p, [0, 1], [24, 0]) };
  }
  const sKwp = statSpring(0);
  const sSav = statSpring(STATS_STAGGER);
  const sPay = statSpring(STATS_STAGGER * 2);
  const sCo2 = statSpring(STATS_STAGGER * 3);
  const outroOpacity = interpolate(
    frame,
    [STATS_START - 4, STATS_START + 20],
    [0, 1],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
  );
  const formattedSavings = Math.round(yearlySavingsEur).toLocaleString('it-IT');

  return (
    <>
      <AbsoluteFill
        style={{
          pointerEvents: 'none',
          opacity: outroOpacity,
          background:
            'linear-gradient(to top, rgba(0,0,0,0.82) 0%, rgba(0,0,0,0.55) 25%, rgba(0,0,0,0) 60%)',
        }}
      />
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
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={brandLogoUrl}
            alt=""
            style={{
              maxWidth: 220,
              maxHeight: 80,
              objectFit: 'contain',
              filter: 'drop-shadow(0 2px 8px rgba(0,0,0,0.7))',
            }}
          />
        </AbsoluteFill>
      ) : null}
    </>
  );
}

// ── Composition entry ─────────────────────────────────────────────────────

export const SolarTransition3D: React.FC<Props> = (props) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();

  return (
    <AbsoluteFill style={{ background: '#050912' }}>
      <ThreeCanvas
        width={width}
        height={height}
        gl={{ antialias: true, toneMapping: THREE.ACESFilmicToneMapping }}
        shadows
      >
        <Scene scene={props.scene3d} frame={frame} fps={fps} />
      </ThreeCanvas>
      <StatsOverlay
        kwp={props.kwp}
        yearlySavingsEur={props.yearlySavingsEur}
        paybackYears={props.paybackYears}
        co2TonnesLifetime={props.co2TonnesLifetime}
        tenantName={props.tenantName}
        brandPrimaryColor={props.brandPrimaryColor}
        brandLogoUrl={props.brandLogoUrl}
        frame={frame}
        fps={fps}
      />
    </AbsoluteFill>
  );
};
