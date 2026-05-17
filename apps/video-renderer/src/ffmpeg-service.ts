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
 * Bundled ffmpeg via @ffmpeg-installer/ffmpeg so we don't depend on the
 * Docker host's apt package version (Debian slim ships 5.x; we want a
 * known-good binary).
 */
import path from 'node:path';
import { promises as fs } from 'node:fs';
import { spawn } from 'node:child_process';

import ffmpegInstaller from '@ffmpeg-installer/ffmpeg';

const FFMPEG_BIN: string = ffmpegInstaller.path;

/** Verde del bagliore dell'overlay (emerald). Non è il brand color del
 *  tenant (spesso navy): il "verde = risparmio" è una costante voluta. */
const GLOW_COLOR = '0x16A34A';

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
 * Build the `-filter_complex` graph that reveals the stats card over
 * the last ~1.8s of the clip:
 *   - input [1:v] is a tiny green lavfi `color` source; `geq` paints a
 *     vertical alpha gradient on it (transparent at the top, solid
 *     toward the bottom), it is scaled to the video and faded in → a
 *     green light that rises from below, no hard black rectangle;
 *   - 3 bold text lines (label · big savings figure · kW + payback)
 *     fade in just after — white, with a dark-green edge + drop shadow
 *     so they stay readable on any rooftop.
 *
 * `clipDurationS` is the wall-clock length of the source clip; the
 * reveal occupies its last `reveal` seconds. The graph ends on [out].
 */
export const buildOverlayFilter = (
  stats: OverlayStats,
  clipDurationS: number,
  fontFile?: string,
): string => {
  const reveal = Math.min(1.8, Math.max(0.9, clipDurationS - 0.4));
  const fadeStart = Math.max(0, clipDurationS - reveal);

  const savings = formatEuro(stats.yearlySavingsEur);
  const kw = `${Math.round(stats.kwp * 10) / 10} kW`;
  const payback = `rientro in ~${Math.max(1, Math.round(stats.paybackYears))} anni`;
  const sub = `${kw}  ·  ${payback}`;
  const label = 'RISPARMIO STIMATO ALL’ANNO';

  // Per-line alpha: 0 until `st`, then ramps to 1 over 0.5s.
  const txtAlpha = (st: number): string =>
    `alpha='if(lt(t\\,${st})\\,0\\,if(lt(t\\,${st + 0.5})\\,(t-${st})/0.5\\,1))'`;

  const fontPart = fontFile ? `fontfile=${fontFile}:` : '';
  const common =
    `${fontPart}fontcolor=white:borderw=2:bordercolor=0x06351A` +
    `:shadowcolor=0x04200D@0.85:shadowx=0:shadowy=3:x=(w-text_w)/2`;

  // Green glow: tiny color source → vertical alpha gradient → scaled to
  // the video → faded in. `geq` runs on a 2×256 source (negligible).
  const glow =
    `[1:v]format=rgba,` +
    `geq=r='r(X\\,Y)':g='g(X\\,Y)':b='b(X\\,Y)':` +
    `a='clip((Y-80)/90\\,0\\,1)*230'[grad];` +
    `[grad][0:v]scale2ref=w=iw:h=ih[grad2][base];` +
    `[grad2]fade=t=in:st=${fadeStart}:d=0.6:alpha=1[glow];` +
    `[base][glow]overlay=0:0[lit]`;

  const lineLabel =
    `[lit]drawtext=text='${escapeDrawtext(label)}':fontsize=30:` +
    `${common}:${txtAlpha(fadeStart + 0.15)}:y=h-258[t1]`;
  const lineValue =
    `[t1]drawtext=text='${escapeDrawtext(savings)}':fontsize=94:` +
    `${common}:${txtAlpha(fadeStart + 0.2)}:y=h-222[t2]`;
  const lineSub =
    `[t2]drawtext=text='${escapeDrawtext(sub)}':fontsize=34:` +
    `${common}:${txtAlpha(fadeStart + 0.3)}:y=h-104[out]`;

  return [glow, lineLabel, lineValue, lineSub].join(';');
};

/**
 * Burn the overlay into `inputMp4Path` and write the result to
 * `outputMp4Path`. Re-encodes with libx264 CRF 22 (visually lossless
 * for our use case while keeping file size reasonable for portal
 * playback). A second lavfi input feeds the green-glow gradient.
 */
export const overlayStatsOnVideo = async (
  inputMp4Path: string,
  outputMp4Path: string,
  stats: OverlayStats,
  clipDurationS: number,
): Promise<void> => {
  const fontFile = await resolveBoldFont();
  const filter = buildOverlayFilter(stats, clipDurationS, fontFile);
  await runFfmpeg([
    '-y',
    '-i',
    inputMp4Path,
    '-f',
    'lavfi',
    '-i',
    `color=c=${GLOW_COLOR}:s=2x256:d=${Math.max(1, clipDurationS)}`,
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
 * High-level entry: download the Replicate URL, burn overlay, produce
 * GIF. Returns local paths; the caller uploads to Supabase + cleans up.
 */
export const postProcessVideo = async (
  videoUrl: string,
  workDir: string,
  stats: OverlayStats,
  clipDurationS: number,
): Promise<PostProcessResult> => {
  const rawPath = path.join(workDir, 'raw.mp4');
  const mp4Path = path.join(workDir, 'transition.mp4');
  const gifPath = path.join(workDir, 'transition.gif');

  await downloadVideo(videoUrl, rawPath);
  await overlayStatsOnVideo(rawPath, mp4Path, stats, clipDurationS);
  await convertToGif(mp4Path, gifPath);

  return { mp4Path, gifPath };
};
