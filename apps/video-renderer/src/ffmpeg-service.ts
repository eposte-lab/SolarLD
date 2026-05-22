/**
 * FFmpeg post-processing — takes the raw MP4 from Replicate and:
 *   1. Burns a subtle "stats card" overlay over the last ~1.5s of the
 *      clip (kWp / annual savings / payback) so the recipient sees the
 *      financial promise the moment the panels are fully revealed.
 *   2. Re-encodes a Gmail-friendly GIF (480×480, 12fps, palette-optimized)
 *      typically 2-4 MB — well under Gmail's ~10 MB inline ceiling that
 *      bit us with the Remotion 27 MB GIF.
 *
 * Why drawtext over a PNG overlay: drawtext is a single ffmpeg pass
 * with no extra asset to ship (no font fetch, no Canvas dependency in
 * Node). Trade-off: limited typography, but for 3 lines of stats on a
 * dark gradient it looks clean enough.
 *
 * FFmpeg binary resolution: FFMPEG_PATH wins (the Docker image sets it
 * to the system ffmpeg, 5.1.x on bookworm). It falls back to the
 * @ffmpeg-installer binary for local dev. The system binary is
 * required in production because @ffmpeg-installer ships a 2018 build
 * whose libavfilter predates the `xfade` filter (ffmpeg 4.3, 2020)
 * used by the crossfade transition.
 */
import path from 'node:path';
import { promises as fs } from 'node:fs';
import { spawn } from 'node:child_process';

import ffmpegInstaller from '@ffmpeg-installer/ffmpeg';

const FFMPEG_BIN: string = process.env.FFMPEG_PATH ?? ffmpegInstaller.path;

/** Altezza della striscia inferiore dell'overlay, in pixel su un video
 *  alto 720. Una lower-third sottile e discreta: niente bagliori,
 *  niente numeri giganti — deve restare professionale e non invasiva. */
const STRIP_H = 104;

/** Opacità della striscia: abbastanza coprente da far leggere il brand
 *  color del tenant come una barra piena, non come una velatura. */
const STRIP_ALPHA = 0.72;

/** Converte un colore brand "#RRGGBB" nel formato che ffmpeg accetta
 *  per `color=c=` (`0xRRGGBB`). Degrada a un navy scuro se il valore
 *  non è un esadecimale a 6 cifre. */
const ffmpegColor = (hex: string): string => {
  const h = (hex ?? '').replace(/^#/, '').trim();
  return /^[0-9a-fA-F]{6}$/.test(h) ? `0x${h}` : '0x101a2e';
};

/** Font in grassetto per l'overlay. `fonts-dejavu-core` (installato nel
 *  Dockerfile) fornisce DejaVuSans-Bold; in locale può non esserci →
 *  `resolveBoldFont` degrada al font di default di ffmpeg. */
const BOLD_FONT_CANDIDATES: string[] = [
  process.env.OVERLAY_FONT_FILE ?? '',
  '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
].filter(Boolean);

export interface OverlayStats {
  kwp: number;
  yearlySavingsEur: number;
  paybackYears: number;
  tenantName: string;
  brandPrimaryColor: string;
}

export interface PostProcessResult {
  /** Local path to the MP4 with overlay burned in. */
  mp4Path: string;
  /** Local path to the optimized GIF. */
  gifPath: string;
}

/**
 * Pull the raw MP4 from a Replicate signed URL into the working dir.
 * The URL expires ~1h after generation so we always materialize first.
 */
export const downloadVideo = async (url: string, destPath: string): Promise<void> => {
  const resp = await fetch(url);
  if (!resp.ok) {
    throw new Error(`download failed: ${resp.status} ${resp.statusText}`);
  }
  const buf = Buffer.from(await resp.arrayBuffer());
  await fs.writeFile(destPath, buf);
};

/**
 * Run ffmpeg with the given args, streaming stderr to console for
 * visibility. Resolves on exit-code 0, rejects otherwise with the tail
 * of stderr (full output is too noisy to log on success).
 */
export const runFfmpeg = (args: string[]): Promise<void> =>
  new Promise((resolve, reject) => {
    const proc = spawn(FFMPEG_BIN, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    const stderrChunks: Buffer[] = [];
    proc.stderr.on('data', (chunk: Buffer) => {
      stderrChunks.push(chunk);
    });
    proc.on('error', reject);
    proc.on('close', (code) => {
      if (code === 0) {
        resolve();
        return;
      }
      const tail = Buffer.concat(stderrChunks).toString('utf8').slice(-2000);
      reject(new Error(`ffmpeg exited ${code}\n${tail}`));
    });
  });

/**
 * Resolve a bold font file for the overlay. Returns the first existing
 * candidate, or `undefined` (→ ffmpeg's built-in default font).
 */
export const resolveBoldFont = async (): Promise<string | undefined> => {
  for (const candidate of BOLD_FONT_CANDIDATES) {
    try {
      await fs.access(candidate);
      return candidate;
    } catch {
      /* try the next candidate */
    }
  }
  return undefined;
};

/**
 * Format euro amounts for the overlay: full Italian thousands grouping
 * up to 999.999 ("€2.400"), compact "M" above ("€1.2M"). The big
 * savings figure has to read as money, not as a cryptic "2.4k".
 */
export const formatEuro = (n: number): string => {
  if (!Number.isFinite(n) || n <= 0) return '—';
  if (n >= 1_000_000) return `€${(n / 1_000_000).toFixed(1)}M`;
  return `€${Math.round(n)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, '.')}`;
};

/**
 * Escape a string for ffmpeg's drawtext filter. Colons, single quotes,
 * backslashes and percent signs all need escaping; Unicode passes
 * through (drawtext is UTF-8 native if the font supports it).
 */
export const escapeDrawtext = (s: string): string =>
  s
    .replace(/\\/g, '\\\\')
    .replace(/:/g, '\\:')
    .replace(/'/g, "\\'")
    .replace(/%/g, '\\%')
    .replace(/,/g, '\\,');

/**
 * Build the `-filter_complex` graph for a discreet professional
 * lower-third:
 *   - a thin translucent dark strip fades IN over the last ~1.8s of
 *     the clip (in sync with the end of the wipe) — it is not present
 *     during the reveal, so the rooftop stays clean while the panels
 *     appear;
 *   - 2 small text lines (uppercase label · one compact value line:
 *     savings · kW) fade in just after the strip.
 *
 * No green glow, no oversized figures, no payback figure. Input [1:v]
 * is a pre-sized lavfi strip in the tenant's brand colour (1920px
 * wide — wider than any frame we render, the overlay clips the
 * excess; this avoids the fragile scale2ref dance that mis-sized the
 * strip). `format=rgba` + `fade` ramp its alpha in, then it is
 * overlaid flush with the bottom edge. The graph ends on [out].
 *
 * `clipDurationS` is the wall-clock length of the clip.
 */
export const buildOverlayFilter = (
  stats: OverlayStats,
  clipDurationS: number,
  fontFile?: string,
): string => {
  const fadeStart = Math.max(0.2, clipDurationS - 1.8);

  const savings = formatEuro(stats.yearlySavingsEur);
  const kw = `${Math.round(stats.kwp * 10) / 10} kW`;
  const value = `${savings}   ·   ${kw}`;
  const label = 'RISPARMIO ANNUO STIMATO';

  // Per-line alpha: 0 until `st`, then ramps to 1 over 0.5s.
  const txtAlpha = (st: number): string =>
    `alpha='if(lt(t\\,${st})\\,0\\,if(lt(t\\,${st + 0.5})\\,(t-${st})/0.5\\,1))'`;

  const fontPart = fontFile ? `fontfile=${fontFile}:` : '';

  // [1:v] is the pre-sized translucent strip. Just fade its alpha in
  // and overlay it flush with the bottom edge — no scaling needed.
  const strip =
    `[1:v]format=rgba,fade=t=in:st=${fadeStart}:d=0.6:alpha=1[barf];` +
    `[0:v][barf]overlay=0:H-h:shortest=1[lit]`;

  // Small uppercase label, then the compact value line — left-aligned
  // with a 48px gutter. White text; the strip provides the contrast.
  const lineLabel =
    `[lit]drawtext=text='${escapeDrawtext(label)}':fontsize=21:` +
    `${fontPart}fontcolor=white@0.66:x=48:y=h-${STRIP_H}+26:` +
    `${txtAlpha(fadeStart + 0.2)}[t1]`;
  const lineValue =
    `[t1]drawtext=text='${escapeDrawtext(value)}':fontsize=34:` +
    `${fontPart}fontcolor=white:x=48:y=h-${STRIP_H}+52:` +
    `${txtAlpha(fadeStart + 0.35)}[out]`;

  return [strip, lineLabel, lineValue].join(';');
};

/**
 * Burn the overlay into `inputMp4Path` and write the result to
 * `outputMp4Path`. Re-encodes with libx264 CRF 22 (visually lossless
 * for our use case while keeping file size reasonable for portal
 * playback). A second lavfi input feeds the pre-sized strip
 * (1920×STRIP_H) tinted with the tenant's brand colour.
 */
export const overlayStatsOnVideo = async (
  inputMp4Path: string,
  outputMp4Path: string,
  stats: OverlayStats,
  clipDurationS: number,
): Promise<void> => {
  const fontFile = await resolveBoldFont();
  const filter = buildOverlayFilter(stats, clipDurationS, fontFile);
  const stripColor = ffmpegColor(stats.brandPrimaryColor);
  await runFfmpeg([
    '-y',
    '-i',
    inputMp4Path,
    '-f',
    'lavfi',
    '-i',
    `color=c=${stripColor}@${STRIP_ALPHA}:s=1920x${STRIP_H}:d=${Math.max(1, clipDurationS)}`,
    '-filter_complex',
    filter,
    '-map',
    '[out]',
    '-c:v',
    'libx264',
    '-preset',
    'medium',
    '-crf',
    '22',
    '-pix_fmt',
    'yuv420p',
    '-movflags',
    '+faststart',
    '-an', // source clips have no audio; drop the track explicitly.
    outputMp4Path,
  ]);
};

/**
 * Produce a Gmail-friendly GIF from the post-overlay MP4.
 *
 * We use the classic two-pass palettegen / paletteuse trick: a single
 * palette is computed for the whole clip then applied frame-by-frame.
 * Without it, ffmpeg's default 256-color quantization per-frame
 * produces banded gradients and 3-4× larger files.
 *
 * Targets:
 *   - 1280×720 (16:9) — allineato al formato delle immagini start/end
 *     e del crossfade; niente crop verticale quando il rendering è
 *     mostrato a 16:9 su portale e dashboard
 *   - 15 fps (was 12, Replicate native is 24-30) — 15 is the perceptual
 *     sweet spot for a slow reveal: anything <12 stutters visibly,
 *     anything >18 inflates filesize without buying smoothness
 *   - typical output: 4-6 MB for a 5s clip, ~9-12 MB for 10s
 *     (Gmail caps inline at ~25 MB so we have headroom)
 */
export const convertToGif = async (
  inputMp4Path: string,
  outputGifPath: string,
): Promise<void> => {
  const filterComplex =
    'fps=15,scale=1280:720:flags=lanczos,split[s0][s1];' +
    '[s0]palettegen=stats_mode=diff[p];' +
    '[s1][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle';

  await runFfmpeg([
    '-y',
    '-i',
    inputMp4Path,
    '-filter_complex',
    filterComplex,
    '-loop',
    '0', // 0 = loop forever (Gmail-friendly)
    outputGifPath,
  ]);
};

/**
 * High-level entry: download the Replicate URL, produce GIF. Returns
 * local paths; the caller uploads to Supabase + cleans up.
 *
 * Nota: `stats` e `clipDurationS` restano nella signature per non
 * rompere i caller, ma l'overlay ffmpeg è disabilitato — il "Risparmio
 * annuo" è ora bakeato nell'after.png server-side
 * (`solar_rendering_service.bake_savings_strip`).
 */
export const postProcessVideo = async (
  videoUrl: string,
  workDir: string,
  _stats: OverlayStats,
  _clipDurationS: number,
): Promise<PostProcessResult> => {
  const mp4Path = path.join(workDir, 'transition.mp4');
  const gifPath = path.join(workDir, 'transition.gif');

  await downloadVideo(videoUrl, mp4Path);
  await convertToGif(mp4Path, gifPath);

  return { mp4Path, gifPath };
};
