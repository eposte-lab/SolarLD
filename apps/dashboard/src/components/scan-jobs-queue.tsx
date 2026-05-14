'use client';

/**
 * ScanJobsQueue — pannello destro della pagina /territorio.
 *
 * Lista delle scansioni del tenant ordinata per priority ASC (top =
 * prossima consumata). Drag-drop nativo HTML5 per riordinare. Ogni
 * card mostra status, counter del giorno, totale lead generati e
 * azioni rapide (pausa, rilancia, archivia, modifica).
 */

import { useState, type DragEvent } from 'react';

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
  { emoji: string; label: string; tone: string }
> = {
  pending: { emoji: '⏳', label: 'in coda', tone: 'bg-surface-container text-on-surface-variant' },
  in_progress: { emoji: '🟢', label: 'in corso', tone: 'bg-primary-container text-on-primary-container' },
  paused: { emoji: '⏸', label: 'in pausa', tone: 'bg-amber-100 text-amber-900' },
  paused_daily_cap: { emoji: '🟡', label: 'cap giornaliero', tone: 'bg-amber-100 text-amber-900' },
  exhausted: { emoji: '✅', label: 'esaurita', tone: 'bg-tertiary-container text-on-tertiary-container' },
  archived: { emoji: '📦', label: 'archiviata', tone: 'bg-surface-container text-on-surface-variant' },
};

type Props = {
  jobs: ScanJob[];
  onMutate: () => void;
};

export function ScanJobsQueue({ jobs, onMutate }: Props) {
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    return [job.comune, job.province, job.region].filter(Boolean).join(' · ');
  }

  if (jobs.length === 0) {
    return (
      <div className="rounded-2xl bg-surface-container-low p-12 text-center ring-1 ring-on-surface/5">
        <p className="text-2xl" aria-hidden>🗂️</p>
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
        <p className="rounded-md bg-error-container px-3 py-2 text-xs text-on-error-container">
          ⚠ {error}
        </p>
      )}
      <ul className="space-y-2">
        {jobs.map((job) => {
          const meta = STATUS_META[job.status];
          const capPercent = Math.min(100, Math.round((job.valid_leads_today / job.daily_validated_cap) * 100));
          const isDragging = draggedId === job.id;
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
                  ⋮⋮
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <h4 className="truncate font-headline text-base font-semibold tracking-tight text-on-surface">
                      {job.name}
                    </h4>
                    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest ${meta.tone}`}>
                      <span aria-hidden>{meta.emoji}</span> {meta.label}
                    </span>
                    {job.always_active && (
                      <span className="rounded-full bg-secondary-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-on-secondary-container">
                        ♾ sempre attivo
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 text-xs text-on-surface-variant">
                    🗺️ {territoryLabel(job) || '—'} ·{' '}
                    {job.sector_filters.length === 0
                      ? 'tutti i settori'
                      : job.sector_filters.map((s) => SECTOR_LABELS[s] ?? s).join(', ')}
                  </p>

                  {/* Progress bar daily cap */}
                  <div className="mt-2.5 space-y-1">
                    <div className="flex items-baseline justify-between text-[11px] text-on-surface-variant">
                      <span>
                        <strong className="text-on-surface tabular-nums">{job.valid_leads_today}</strong>
                        /{job.daily_validated_cap.toLocaleString('it-IT')} oggi
                      </span>
                      <span>
                        Totale: <strong className="text-on-surface tabular-nums">{job.valid_leads_total.toLocaleString('it-IT')}</strong>
                      </span>
                    </div>
                    <div className="h-1.5 w-full rounded-full bg-surface-container">
                      <div
                        className="h-full rounded-full bg-primary transition-all"
                        style={{ width: `${capPercent}%` }}
                      />
                    </div>
                  </div>

                  {job.last_error && (
                    <p className="mt-2 truncate text-[11px] text-error" title={job.last_error}>
                      ⚠ {job.last_error}
                    </p>
                  )}

                  <div className="mt-2.5 flex flex-wrap gap-1.5">
                    {job.status === 'exhausted' ? (
                      <button
                        type="button"
                        onClick={() => relaunch(job)}
                        className="rounded-full bg-primary px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest text-on-primary hover:opacity-90"
                      >
                        ↻ Rilancia
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => togglePause(job)}
                        className="rounded-full bg-surface-container px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest text-on-surface hover:bg-surface-container-high"
                      >
                        {job.status === 'paused' ? '▶ Riprendi' : '⏸ Pausa'}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => archive(job)}
                      className="rounded-full bg-surface-container px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant hover:bg-error-container hover:text-on-error-container"
                    >
                      📦 Archivia
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
