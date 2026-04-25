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
const runFfmpeg = (args: string[]): Promise<void> =>
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
 * Format euro amounts compactly so the overlay never wraps on a 1080
 * frame (e.g. "€2.400/anno", "€1.230.000/anno" → "€1.2M/anno").
 */
export const formatEuro = (n: number): string => {
  if (!Number.isFinite(n) || n <= 0) return '—';
  if (n >= 1_000_000) return `€${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `€${Math.round(n / 100) / 10}k`;
  return `€${Math.round(n)}`;
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
 * Build the drawtext filter chain that fades in the stats card during
 * the last 1.5s of the clip. We layer:
 *   - a translucent dark band at the bottom (drawbox)
 *   - 3 lines of stat text (drawtext, faded in via alpha=if(...))
 *
 * `clipDurationS` is the wall-clock length of the source video (5 or
 * 10s for Kling). The overlay fades in over `clipDurationS-1.5`.
 */
export const buildOverlayFilter = (stats: OverlayStats, clipDurationS: number): string => {
  const fadeStart = Math.max(0, clipDurationS - 1.5);
  const k = `${Math.round(stats.kwp * 10) / 10} kWp`;
  const savings = `${formatEuro(stats.yearlySavingsEur)}/anno risparmio`;
  const payback = `Rientro in ~${Math.round(stats.paybackYears * 10) / 10} anni`;

  // Alpha expression: 0 before fadeStart, ramps 0→1 over 0.5s.
  const alpha = `if(lt(t\\,${fadeStart})\\,0\\,if(lt(t\\,${fadeStart + 0.5})\\,(t-${fadeStart})/0.5\\,1))`;

  // Fontfile not specified → ffmpeg falls back to its default sans
  // (DejaVu on Debian slim, which @ffmpeg-installer ships independently).
  const drawbox = `drawbox=x=0:y=ih-180:w=iw:h=180:color=black@0.55:t=fill:enable='gte(t,${fadeStart})'`;

  const baseTxt =
    `:fontcolor=white:borderw=2:bordercolor=black@0.6` +
    `:x=(w-text_w)/2:alpha='${alpha}'`;

  const line1 = `drawtext=text='${escapeDrawtext(k)}':fontsize=64:y=h-160${baseTxt}`;
  const line2 = `drawtext=text='${escapeDrawtext(savings)}':fontsize=42:y=h-90${baseTxt}`;
  const line3 = `drawtext=text='${escapeDrawtext(payback)}':fontsize=34:y=h-44${baseTxt}`;

  return [drawbox, line1, line2, line3].join(',');
};

/**
 * Burn the overlay into `inputMp4Path` and write the result to
 * `outputMp4Path`. Re-encodes with libx264 CRF 22 (visually lossless
 * for our use case while keeping file size reasonable for portal
 * playback).
 */
export const overlayStatsOnVideo = async (
  inputMp4Path: string,
  outputMp4Path: string,
  stats: OverlayStats,
  clipDurationS: number,
): Promise<void> => {
  const filter = buildOverlayFilter(stats, clipDurationS);
  await runFfmpeg([
    '-y',
    '-i',
    inputMp4Path,
    '-vf',
    filter,
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
    '-an', // Replicate clips have no audio; drop the track explicitly.
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
 *   - 480×480 (down from 1080) — Gmail re-scales above 600px anyway
 *   - 12fps (down from Replicate's 24-30fps) — plenty for a slow reveal
 *   - typical output: 2-4 MB for a 5s clip, ~5-7 MB for 10s
 */
export const convertToGif = async (
  inputMp4Path: string,
  outputGifPath: string,
): Promise<void> => {
  const filterComplex =
    'fps=12,scale=480:480:flags=lanczos,split[s0][s1];' +
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
