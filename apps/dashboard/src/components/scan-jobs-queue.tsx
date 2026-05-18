'use client';

/**
 * ScanJobsQueue — pannello destro della pagina /territorio.
 *
 * Lista delle scansioni del tenant ordinata per priority ASC (top =
 * prossima consumata). Drag-drop nativo HTML5 per riordinare. Ogni
 * card mostra status, counter del giorno, totale lead generati e
 * azioni rapide (pausa, rilancia, archivia, modifica).
 */

import {
  AlertTriangle,
  Archive,
  GripVertical,
  Inbox,
  MapPin,
  Pause,
  Play,
  Repeat,
  RotateCw,
} from 'lucide-react';
import { useEffect, useState, type DragEvent } from 'react';

import {
  deleteScanJob,
  reorderScanJobs,
  updateScanJob,
  type ScanJob,
} from '@/lib/data/scan-jobs';
import { SECTOR_LABELS } from '@/lib/sector-labels';
import { relativeTime } from '@/lib/utils';

const STATUS_META: Record<
  ScanJob['status'],
  { label: string; tone: string }
> = {
  pending: { label: 'In coda', tone: 'bg-surface-container text-on-surface-variant' },
  in_progress: { label: 'In corso', tone: 'bg-primary-container text-on-primary-container' },
  paused: { label: 'In pausa', tone: 'bg-amber-100 text-amber-900' },
  paused_daily_cap: { label: 'Cap giornaliero', tone: 'bg-amber-100 text-amber-900' },
  exhausted: { label: 'Esaurita', tone: 'bg-tertiary-container text-on-tertiary-container' },
  completed: { label: 'Completata', tone: 'bg-emerald-100 text-emerald-900' },
  archived: { label: 'Archiviata', tone: 'bg-surface-container text-on-surface-variant' },
};

/** Elapsed time "Xm Ys" / "Xh Ym" between an ISO instant and now (ms). */
function formatElapsed(fromIso: string, nowMs: number): string {
  const secs = Math.max(0, Math.floor((nowMs - new Date(fromIso).getTime()) / 1000));
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  if (m < 60) return `${m}m ${secs % 60}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

type Props = {
  jobs: ScanJob[];
  onMutate: () => void;
};

export function ScanJobsQueue({ jobs, onMutate }: Props) {
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Live clock — ticks every second only while a scan is running, so
  // the elapsed timers on in-progress cards count up in real time.
  const [now, setNow] = useState(() => Date.now());
  const anyRunning = jobs.some((j) => j.status === 'in_progress');
  useEffect(() => {
    if (!anyRunning) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [anyRunning]);

  function onDragStart(e: DragEvent<HTMLLIElement>, id: string) {
    setDraggedId(id);
    e.dataTransfer.effectAllowed = 'move';
  }

  function onDragOver(e: DragEvent<HTMLLIElement>) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  }

  async function onDrop(e: DragEvent<HTMLLIElement>, targetId: string) {
    e.preventDefault();
    if (!draggedId || draggedId === targetId) return;

    const fromIdx = jobs.findIndex((j) => j.id === draggedId);
    const toIdx = jobs.findIndex((j) => j.id === targetId);
    if (fromIdx < 0 || toIdx < 0) return;

    const reordered = [...jobs];
    const [moved] = reordered.splice(fromIdx, 1);
    if (!moved) return;
    reordered.splice(toIdx, 0, moved);

    try {
      await reorderScanJobs(reordered.map((j) => j.id));
      onMutate();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'reorder_failed');
    } finally {
      setDraggedId(null);
    }
  }

  async function togglePause(job: ScanJob) {
    const next: ScanJob['status'] =
      job.status === 'paused' ? 'pending' : 'paused';
    try {
      await updateScanJob(job.id, { status: next });
      onMutate();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'update_failed');
    }
  }

  async function relaunch(job: ScanJob) {
    try {
      await updateScanJob(job.id, { status: 'pending' });
      onMutate();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'relaunch_failed');
    }
  }

  async function archive(job: ScanJob) {
    if (!confirm(`Archiviare "${job.name}"? I lead già scaricati restano in /leads.`)) return;
    try {
      await deleteScanJob(job.id);
      onMutate();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'archive_failed');
    }
  }

  function territoryLabel(job: ScanJob): string {
    const pcs = job.province_codes ?? [];
    const provPart =
      pcs.length === 0
        ? ''
        : pcs.length <= 4
          ? pcs.join(', ')
          : `${pcs.length} province`;
    return [job.region, provPart].filter(Boolean).join(' · ');
  }

  if (jobs.length === 0) {
    return (
      <div className="rounded-2xl bg-surface-container-low p-12 text-center ring-1 ring-on-surface/5">
        <Inbox
          size={28}
          strokeWidth={1.75}
          className="mx-auto text-on-surface-variant"
          aria-hidden
        />
        <p className="mt-3 font-headline text-base font-semibold text-on-surface">
          Nessuna scansione ancora
        </p>
        <p className="mt-1.5 text-sm text-on-surface-variant">
          Crea la prima scansione dal pannello a sinistra per iniziare a raccogliere lead.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {error && (
        <p className="flex items-center gap-1.5 rounded-md bg-error-container px-3 py-2 text-xs text-on-error-container">
          <AlertTriangle size={13} strokeWidth={2} aria-hidden /> {error}
        </p>
      )}
      <ul className="space-y-2">
        {jobs.map((job) => {
          const meta = STATUS_META[job.status];
          const capPercent = Math.min(100, Math.round((job.valid_leads_today / job.daily_validated_cap) * 100));
          const totalPercent = job.total_validated_cap > 0
            ? Math.min(100, Math.round((job.valid_leads_total / job.total_validated_cap) * 100))
            : 0;
          const isDragging = draggedId === job.id;
          const running = job.status === 'in_progress';
          // No last_run_at yet while in progress → still in the L0
          // territory-mapping phase (the funnel sets last_run_at).
          const mapping = running && !job.last_run_at;
          const startIso = job.last_run_at ?? job.created_at;
          return (
            <li
              key={job.id}
              draggable
              onDragStart={(e) => onDragStart(e, job.id)}
              onDragOver={onDragOver}
              onDrop={(e) => onDrop(e, job.id)}
              className={`rounded-2xl bg-surface-container-lowest p-4 ring-1 ring-on-surface/5 transition ${isDragging ? 'opacity-40' : 'hover:ring-on-surface/15'}`}
            >
              <div className="flex items-start gap-3">
                <span
                  className="mt-1 cursor-grab text-on-surface-variant hover:text-on-surface active:cursor-grabbing"
                  aria-label="Trascina per riordinare"
                >
                  <GripVertical size={16} strokeWidth={2} aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h4 className="truncate font-headline text-base font-semibold tracking-tight text-on-surface">
                      {job.name}
                    </h4>
                    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest ${meta.tone}`}>
                      <span
                        className="h-1.5 w-1.5 rounded-full bg-current opacity-70"
                        aria-hidden
                      />
                      {meta.label}
                    </span>
                    {job.always_active && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-secondary-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-on-secondary-container">
                        <Repeat size={10} strokeWidth={2.25} aria-hidden />
                        Sempre attivo
                      </span>
                    )}
                  </div>
                  <p className="mt-1 flex items-start gap-1.5 text-xs text-on-surface-variant">
                    <MapPin
                      size={13}
                      strokeWidth={2}
                      className="mt-px shrink-0"
                      aria-hidden
                    />
                    <span>
                      {territoryLabel(job) || '—'} ·{' '}
                      {job.sector_filters.length === 0
                        ? 'tutti i settori'
                        : job.sector_filters.map((s) => SECTOR_LABELS[s] ?? s).join(', ')}
                    </span>
                  </p>

                  {/* Cosa sta facendo la scansione adesso */}
                  <p className="mt-1.5 flex items-center gap-1.5 text-[11px] text-on-surface-variant">
                    {running && (
                      <span className="relative flex h-1.5 w-1.5 shrink-0" aria-hidden>
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75" />
                        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
                      </span>
                    )}
                    <span>
                      {mapping
                        ? `Mappatura del territorio · ${formatElapsed(startIso, now)}`
                        : running
                          ? `Scansione in corso · ${formatElapsed(startIso, now)} · ${job.candidates_scanned_total.toLocaleString('it-IT')} candidati analizzati`
                          : job.status === 'completed'
                            ? 'Completata — raggiunto il tetto di lead totali'
                            : job.status === 'exhausted'
                              ? 'Esaurita — nessun altro candidato nel territorio'
                              : job.status === 'paused_daily_cap'
                              ? 'Tetto giornaliero raggiunto — riprende domani'
                              : job.status === 'paused'
                                ? 'In pausa'
                                : 'In coda — parte appena si libera lo slot'}
                    </span>
                  </p>

                  {/* Progress bar daily cap */}
                  <div className="mt-2.5 space-y-1">
                    <div className="flex items-baseline justify-between text-[11px] text-on-surface-variant">
                      <span>
                        <strong className="text-on-surface tabular-nums">{job.valid_leads_today}</strong>
                        /{job.daily_validated_cap.toLocaleString('it-IT')} oggi
                      </span>
                      <span>{capPercent}%</span>
                    </div>
                    <div className="h-1.5 w-full rounded-full bg-surface-container">
                      <div
                        className="h-full rounded-full bg-primary transition-all"
                        style={{ width: `${capPercent}%` }}
                      />
                    </div>
                  </div>

                  {/* Progress bar total cap */}
                  <div className="mt-2 space-y-1">
                    <div className="flex items-baseline justify-between text-[11px] text-on-surface-variant">
                      <span>
                        <strong className="text-on-surface tabular-nums">{job.valid_leads_total.toLocaleString('it-IT')}</strong>
                        /{job.total_validated_cap.toLocaleString('it-IT')} totali
                      </span>
                      <span>{totalPercent}%</span>
                    </div>
                    <div className="h-1.5 w-full rounded-full bg-surface-container">
                      <div
                        className={`h-full rounded-full transition-all ${job.status === 'completed' ? 'bg-emerald-500' : 'bg-tertiary'}`}
                        style={{ width: `${totalPercent}%` }}
                      />
                    </div>
                  </div>

                  {/* Saturazione del territorio */}
                  {(job.zones_total > 0 || job.candidates_in_queue > 0) && (
                    <p className="mt-2 text-[11px] text-on-surface-variant">
                      {job.candidates_in_queue.toLocaleString('it-IT')} contatti in coda
                      {job.zones_total > 0 && (
                        <> · {job.zones_depleted}/{job.zones_total} zone esaurite</>
                      )}
                    </p>
                  )}

                  {job.last_error && (
                    <p className="mt-2 flex items-center gap-1 truncate text-[11px] text-error" title={job.last_error}>
                      <AlertTriangle size={12} strokeWidth={2} className="shrink-0" aria-hidden />
                      {job.last_error}
                    </p>
                  )}

                  <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                    {job.status === 'exhausted' ? (
                      <button
                        type="button"
                        onClick={() => relaunch(job)}
                        className="inline-flex items-center gap-1 rounded-full bg-primary px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest text-on-primary hover:opacity-90"
                      >
                        <RotateCw size={11} strokeWidth={2.25} aria-hidden />
                        Rilancia
                      </button>
                    ) : job.status === 'completed' ? null : (
                      <button
                        type="button"
                        onClick={() => togglePause(job)}
                        className="inline-flex items-center gap-1 rounded-full bg-surface-container px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest text-on-surface hover:bg-surface-container-high"
                      >
                        {job.status === 'paused' ? (
                          <>
                            <Play size={11} strokeWidth={2.25} aria-hidden />
                            Riprendi
                          </>
                        ) : (
                          <>
                            <Pause size={11} strokeWidth={2.25} aria-hidden />
                            Pausa
                          </>
                        )}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => archive(job)}
                      className="inline-flex items-center gap-1 rounded-full bg-surface-container px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant hover:bg-error-container hover:text-on-error-container"
                    >
                      <Archive size={11} strokeWidth={2.25} aria-hidden />
                      Archivia
                    </button>
                    {job.last_run_at && (
                      <span className="ml-auto text-[10px] text-on-surface-variant">
                        ultimo run {relativeTime(job.last_run_at)}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
