import { describe, it, expect } from 'vitest';

import {
  buildCrossfadeArgs,
  CROSSFADE_DURATION_S,
} from '../ffmpeg-crossfade';

/**
 * Unit test del costruttore di argomenti FFmpeg del crossfade.
 * Funzione pura → testabile senza eseguire FFmpeg né toccare la rete.
 */
describe('buildCrossfadeArgs', () => {
  const args = buildCrossfadeArgs('/tmp/before.png', '/tmp/after.png', '/tmp/out.mp4');
  const joined = args.join(' ');

  it('include entrambe le immagini sorgente e l’output', () => {
    expect(args).toContain('/tmp/before.png');
    expect(args).toContain('/tmp/after.png');
    expect(args).toContain('/tmp/out.mp4');
  });

  it('compone una tendina xfade con zoom Ken Burns', () => {
    const fi = args.indexOf('-filter_complex');
    expect(fi).toBeGreaterThan(-1);
    const filter = args[fi + 1] ?? '';
    expect(filter).toContain('xfade=transition=wipedown');
    expect(filter).toContain('zoompan');
    expect(filter).toContain('[v]');
  });

  it('produce un MP4 H.264 senza traccia audio', () => {
    expect(joined).toContain('libx264');
    expect(joined).toContain('-an');
  });

  it('non chiama nessun modello AI (niente Replicate/Kling)', () => {
    expect(joined.toLowerCase()).not.toContain('replicate');
    expect(joined.toLowerCase()).not.toContain('kling');
  });

  it('la durata totale è di 5 secondi', () => {
    expect(CROSSFADE_DURATION_S).toBe(5);
  });
});
