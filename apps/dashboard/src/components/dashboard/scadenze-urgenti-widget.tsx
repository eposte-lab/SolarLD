/**
 * ScadenzeUrgentiWidget — home-page panel showing open/overdue regulatory
 * deadlines for GSE practices.
 *
 * Server component (no 'use client'): data is fetched at SSR time via
 * listUrgentDeadlines() so the first paint is accurate.
 *
 * Livello 2 Sprint 2.
 */

import Link from 'next/link';
import { AlertTriangle, CalendarClock, CheckCircle2, Clock } from 'lucide-react';

import type { PracticeDeadlineSummary } from '@/lib/data/practices';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function daysUntil(iso: string): number {
  return Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000);
}

function formatDateShort(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('it-IT', {
      day: '2-digit',
      month: 'short',
    });
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function DeadlineRow({ row }: { row: PracticeDeadlineSummary }) {
  const days = daysUntil(row.due_at);
  const isOverdue = row.status === 'overdue' || days < 0;
  const isImminent = !isOverdue && days <= 7;

  return (
    <Link
      href={`/practices/${row.practice_id}`}
      className="group flex items-start gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-surface-container-low"
    >
      {/* Icon */}
      <span
        className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${
          isOverdue
            ? 'bg-rose-100 text-rose-600'
            : isImminent
              ? 'bg-amber-100 text-amber-600'
              : 'bg-blue-50 text-blue-500'
        }`}
      >
        {isOverdue ? (
          <AlertTriangle className="h-3.5 w-3.5" />
        ) : (
          <Clock className="h-3.5 w-3.5" />
        )}
      </span>

      {/* Text */}
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-on-surface">
          {row.title}
        </p>
        <p className="text-xs text-on-surface-variant">
          {row.practice_number && (
            <span className="font-mono">{row.practice_number}&nbsp;·&nbsp;</span>
          )}
          {isOverdue ? (
            <span className="font-semibold text-rose-600">
              {Math.abs(days)} gg in ritardo
            </span>
          ) : (
            <span className={isImminent ? 'font-semibold text-amber-600' : ''}>
              Scade {formatDateShort(row.due_at)}
              {isImminent && ` (${days} gg)`}
            </span>
          )}
        </p>
      </div>
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Widget
// ---------------------------------------------------------------------------

interface Props {
  deadlines: PracticeDeadlineSummary[];
  overdueCount: number;
  openCount: number;
}

export function ScadenzeUrgentiWidget({ deadlines, overdueCount, openCount }: Props) {
  const hasUrgent = overdueCount > 0;
  const total = overdueCount + openCount;

  return (
    <div className="flex h-full flex-col rounded-2xl border border-on-surface/8 bg-white">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-on-surface/8 px-4 py-3">
        <div className="flex items-center gap-2">
          <CalendarClock
            className={`h-4 w-4 ${hasUrgent ? 'text-rose-500' : 'text-on-surface-variant'}`}
          />
          <h3 className="text-sm font-semibold text-on-surface">
            Scadenze GSE
          </h3>
          {total > 0 && (
            <span
              className={`ml-1 rounded-full px-2 py-0.5 text-xs font-bold ${
                hasUrgent
                  ? 'bg-rose-100 text-rose-700'
                  : 'bg-blue-100 text-blue-700'
              }`}
            >
              {total}
            </span>
          )}
        </div>
        <Link
          href="/scadenze"
          className="text-xs font-medium text-primary hover:underline"
        >
          Vedi tutte
        </Link>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {deadlines.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
            <CheckCircle2 className="h-8 w-8 text-emerald-400" />
            <p className="text-sm text-on-surface-variant">
              Nessuna scadenza aperta
            </p>
          </div>
        ) : (
          <div className="divide-y divide-on-surface/5 py-1">
            {deadlines.map((d) => (
              <DeadlineRow key={d.id} row={d} />
            ))}
          </div>
        )}
      </div>

      {/* Footer — summary when there are many more */}
      {total > deadlines.length && (
        <div className="border-t border-on-surface/8 px-4 py-2 text-center">
          <Link
            href="/scadenze"
            className="text-xs text-on-surface-variant hover:text-primary"
          >
            + {total - deadlines.length} altre scadenze
          </Link>
        </div>
      )}
    </div>
  );
}
