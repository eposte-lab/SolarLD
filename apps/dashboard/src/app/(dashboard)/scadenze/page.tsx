/**
 * Scadenze GSE — tenant-wide open / overdue regulatory deadlines.
 *
 * Fetches from GET /v1/practice-deadlines (the server-side projection
 * produced by practice_deadlines_service.DEADLINE_RULES).  One row per
 * (practice, deadline_kind), ordered by urgency (overdue first, then
 * nearest due date).
 *
 * Livello 2 Sprint 2.
 */
'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Loader2,
  RefreshCw,
} from 'lucide-react';

import { BentoCard } from '@/components/ui/bento-card';
import { api, ApiError } from '@/lib/api-client';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PracticeDeadlineRow {
  id: string;
  practice_id: string;
  document_id: string | null;
  deadline_kind: string;
  due_at: string;
  status: 'open' | 'satisfied' | 'overdue' | 'cancelled';
  satisfied_at: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  // Embedded from practices(*) on the backend.
  practice_number: string | null;
  practice_status: string | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function daysUntil(iso: string): number {
  return Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000);
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('it-IT', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    });
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Status chips
// ---------------------------------------------------------------------------

const STATUS_CONFIG: Record<
  PracticeDeadlineRow['status'],
  { label: string; tone: string; icon: typeof Clock }
> = {
  open: {
    label: 'In attesa',
    tone: 'bg-blue-100 text-blue-700',
    icon: Clock,
  },
  satisfied: {
    label: 'Risolta',
    tone: 'bg-emerald-100 text-emerald-700',
    icon: CheckCircle2,
  },
  overdue: {
    label: 'Scaduta',
    tone: 'bg-rose-100 text-rose-700',
    icon: AlertTriangle,
  },
  cancelled: {
    label: 'Annullata',
    tone: 'bg-zinc-100 text-zinc-500',
    icon: Clock,
  },
};

function UrgencyBadge({ row }: { row: PracticeDeadlineRow }) {
  const days = daysUntil(row.due_at);
  if (row.status === 'satisfied') return null;
  if (row.status === 'cancelled') return null;
  if (days < 0) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2 py-0.5 text-xs font-semibold text-rose-700">
        <AlertTriangle className="h-3 w-3" />
        {Math.abs(days)} gg in ritardo
      </span>
    );
  }
  if (days <= 7) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-700">
        <Clock className="h-3 w-3" />
        {days} gg rimasti
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-600">
      <Clock className="h-3 w-3" />
      {days} gg rimasti
    </span>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ScadenzePage() {
  const [rows, setRows] = useState<PracticeDeadlineRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showSatisfied, setShowSatisfied] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.get<PracticeDeadlineRow[]>(
        '/v1/practice-deadlines?limit=200',
      );
      setRows(data);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'Errore caricamento scadenze.',
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  // Sort: overdue first (by how-late), then open by due_at.
  const sorted = useMemo(() => {
    const order: Record<string, number> = {
      overdue: 0,
      open: 1,
      satisfied: 2,
      cancelled: 3,
    };
    return [...rows].sort((a, b) => {
      const oa = order[a.status] ?? 9;
      const ob = order[b.status] ?? 9;
      if (oa !== ob) return oa - ob;
      return new Date(a.due_at).getTime() - new Date(b.due_at).getTime();
    });
  }, [rows]);

  const visible = useMemo(
    () =>
      showSatisfied
        ? sorted
        : sorted.filter((r) => r.status === 'open' || r.status === 'overdue'),
    [sorted, showSatisfied],
  );

  const overdueCount = rows.filter((r) => r.status === 'overdue').length;
  const openCount = rows.filter((r) => r.status === 'open').length;
  const satisfiedCount = rows.filter((r) => r.status === 'satisfied').length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Scadenze regolatorie · {visible.length.toLocaleString('it-IT')}
          </p>
          <h1 className="font-headline text-4xl font-bold tracking-tighter text-on-surface">
            Scadenze
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-on-surface-variant">
            Monitoraggio SLA normativi per le pratiche GSE.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {satisfiedCount > 0 && (
            <button
              onClick={() => setShowSatisfied((v) => !v)}
              className="rounded-full bg-surface-container-high px-3.5 py-1.5 text-xs font-semibold text-on-surface-variant transition-colors hover:bg-surface-container-highest hover:text-on-surface"
            >
              {showSatisfied ? 'Nascondi risolte' : `Mostra risolte (${satisfiedCount})`}
            </button>
          )}
          <button
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-full bg-surface-container-high px-3.5 py-1.5 text-xs font-semibold text-on-surface-variant transition-colors hover:bg-surface-container-highest hover:text-on-surface disabled:opacity-50"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
            Aggiorna
          </button>
        </div>
      </header>

      {/* Summary chips */}
      {!loading && rows.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {overdueCount > 0 && (
            <div className="inline-flex items-center gap-1.5 rounded-full bg-rose-500/15 px-3 py-1.5 text-sm font-semibold text-rose-300">
              <AlertTriangle className="h-4 w-4" />
              {overdueCount} scadut{overdueCount === 1 ? 'a' : 'e'}
            </div>
          )}
          {openCount > 0 && (
            <div className="inline-flex items-center gap-1.5 rounded-full bg-blue-500/15 px-3 py-1.5 text-sm font-medium text-blue-300">
              <Clock className="h-4 w-4" />
              {openCount} in attesa
            </div>
          )}
          {overdueCount === 0 && openCount === 0 && (
            <div className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-3 py-1.5 text-sm font-medium text-emerald-300">
              <CheckCircle2 className="h-4 w-4" />
              Nessuna scadenza aperta
            </div>
          )}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-16 text-on-surface-variant">
          <Loader2 className="h-6 w-6 animate-spin" />
        </div>
      )}

      {/* Error */}
      {error && !loading && (
        <div className="rounded-xl bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && rows.length === 0 && (
        <BentoCard padding="loose" span="full">
          <div className="py-12 text-center text-sm text-on-surface-variant">
            <CheckCircle2 className="mx-auto mb-3 h-10 w-10 text-emerald-400" />
            Nessuna scadenza registrata. Le scadenze appaiono quando invii
            i documenti alle autorità competenti.
          </div>
        </BentoCard>
      )}

      {/* Table */}
      {!loading && visible.length > 0 && (
        <BentoCard padding="tight" span="full" className="overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-on-surface/8 bg-surface-container-lowest/50">
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                  Scadenza
                </th>
                <th className="hidden px-4 py-3 text-left text-xs font-semibold uppercase tracking-widest text-on-surface-variant md:table-cell">
                  Pratica
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                  Stato
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                  Urgenza
                </th>
                <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                  Azione
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-on-surface/6">
              {visible.map((row) => {
                const title =
                  (row.metadata?.['title'] as string | undefined) ??
                  row.deadline_kind;
                const reference = row.metadata?.['reference'] as
                  | string
                  | undefined;
                const cfg = STATUS_CONFIG[row.status];
                const StatusIcon = cfg.icon;
                return (
                  <tr
                    key={row.id}
                    className={cn(
                      'transition-colors hover:bg-surface-container-low',
                      row.status === 'overdue' && 'bg-rose-500/5',
                    )}
                  >
                    {/* Deadline info */}
                    <td className="px-4 py-4">
                      <div className="font-medium text-on-surface">{title}</div>
                      {reference && (
                        <div className="mt-0.5 text-xs italic text-on-surface-variant">
                          {reference}
                        </div>
                      )}
                      <div className="mt-1 text-xs text-on-surface-variant">
                        Scade: {formatDate(row.due_at)}
                        {row.satisfied_at && (
                          <> · risolta {formatDate(row.satisfied_at)}</>
                        )}
                      </div>
                    </td>

                    {/* Practice number */}
                    <td className="hidden px-4 py-4 md:table-cell">
                      <span className="font-mono text-xs text-on-surface">
                        {row.practice_number ?? '—'}
                      </span>
                    </td>

                    {/* Status chip */}
                    <td className="px-4 py-4">
                      <span
                        className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${cfg.tone}`}
                      >
                        <StatusIcon className="h-3 w-3" />
                        {cfg.label}
                      </span>
                    </td>

                    {/* Urgency badge */}
                    <td className="px-4 py-4">
                      <UrgencyBadge row={row} />
                    </td>

                    {/* Action */}
                    <td className="px-4 py-4 text-right">
                      <Link
                        href={`/practices/${row.practice_id}`}
                        className="rounded-md bg-primary/10 px-3 py-1.5 text-xs font-semibold text-primary hover:bg-primary/20"
                      >
                        Apri pratica
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </BentoCard>
      )}
    </div>
  );
}
