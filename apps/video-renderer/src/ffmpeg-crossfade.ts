/**
 * Crossfade locale — anima le immagini start/end con una dissolvenza +
 * leggero zoom (Ken Burns) via FFmpeg, senza modelli AI.
 *
 * Sostituisce la generazione video Kling (~€0,49/clip su Replicate) con
 * un passo di video-editing a costo zero: le due immagini (tetto nudo /
 * tetto con pannelli) sono già prodotte dal CreativeAgent, qui le si fa
 * solo "comparire" l'una sull'altra con uno zoom lento che dà un minimo
 * di movimento.
 *
 * Solo filtri FFmpeg standard (`zoompan`, `xfade`) — nessuna nuova
 * dipendenza, gira ovunque senza GPU.
 */
import path from 'node:path';

import { downloadVideo, runFfmpeg } from './ffmpeg-service';

// Timeline (secondi): la "prima" tiene HOLD_BEFORE, dissolve in
// CROSSFADE, poi la "dopo" tiene HOLD_AFTER.
const HOLD_BEFORE = 1.2;
const CROSSFADE = 2.6;
const HOLD_AFTER = 1.2;
const FPS = 30;
/** Frame finale 16:9 (allineato al GIF prodotto da convertToGif e al
 *  formato delle immagini start/end, già 16:9). */
const OUT_W = 1280;
const OUT_H = 720;
/** Sorgente upscalata sopra l'output così lo zoom-in non sgrana. */
const SRC_W = 2560;
const SRC_H = 1440;

/** Durata totale del video prodotto dal crossfade. */
export const CROSSFADE_DURATION_S = HOLD_BEFORE + CROSSFADE + HOLD_AFTER;

/**
 * Costruisce gli argomenti FFmpeg per il crossfade. Funzione pura
 * (nessun I/O) così è testabile senza eseguire FFmpeg.
 *
 * Ogni immagine diventa una clip con zoom Ken Burns; `xfade=fade` le
 * dissolve. Entrambe le clip durano HOLD_BEFORE+CROSSFADE: l'output di
 * `xfade` è `2·clip − CROSSFADE` = HOLD_BEFORE+CROSSFADE+HOLD_AFTER.
 */
export const buildCrossfadeArgs = (
  beforePath: string,
  afterPath: string,
  outMp4Path: string,
): string[] => {
  const clipLen = HOLD_BEFORE + CROSSFADE;
  // La virgola dentro min() va escapata: nel filter_complex la virgola
  // separa i filtri della catena.
  const zoom = (label: string): string =>
    `[${label}]scale=${SRC_W}:${SRC_H},` +
    `zoompan=z='min(zoom+0.0015\\,1.15)':d=1:` +
    `x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=${OUT_W}x${OUT_H}:fps=${FPS},` +
    `setpts=PTS-STARTPTS`;
  const filter =
    `${zoom('0:v')}[a];${zoom('1:v')}[b];` +
    `[a][b]xfade=transition=fade:duration=${CROSSFADE}:offset=${HOLD_BEFORE}[v]`;

  return [
    '-y',
    '-loop', '1', '-framerate', String(FPS), '-t', String(clipLen), '-i', beforePath,
    '-loop', '1', '-framerate', String(FPS), '-t', String(clipLen), '-i', afterPath,
    '-filter_complex', filter,
    '-map', '[v]',
    '-c:v', 'libx264',
    '-preset', 'medium',
    '-crf', '22',
    '-pix_fmt', 'yuv420p',
    '-r', String(FPS),
    '-movflags', '+faststart',
    '-an',
    outMp4Path,
  ];
};

/**
 * Scarica le due immagini e le compone in un MP4 (dissolvenza + zoom
 * lento) scritto in `outMp4Path`. Nessuna chiamata di rete oltre il
 * download delle immagini.
 */
export const generateCrossfadeVideo = async (
  beforeImageUrl: string,
  afterImageUrl: string,
  workDir: string,
  outMp4Path: string,
): Promise<{ durationS: number }> => {
  const beforePath = path.join(workDir, 'crossfade-before.png');
  const afterPath = path.join(workDir, 'crossfade-after.png');
  await downloadVideo(beforeImageUrl, beforePath);
  await downloadVideo(afterImageUrl, afterPath);

  await runFfmpeg(buildCrossfadeArgs(beforePath, afterPath, outMp4Path));

  return { durationS: CROSSFADE_DURATION_S };
};
