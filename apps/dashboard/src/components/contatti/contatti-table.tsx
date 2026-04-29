'use client';

/**
 * ContattiTable — client wrapper around the contatti list table.
 *
 * Owned by the server component at `app/(dashboard)/contatti/page.tsx`,
 * which prefetches `rows` and passes them in. Sort works within the
 * current paginated page only.
 */

import { SortableTh } from '@/components/ui/sortable-th';
import { useSortableData } from '@/hooks/use-sortable-data';
import { cn, relativeTime } from '@/lib/utils';
import type { ContattoRow } from '@/lib/data/contatti';

const VERDICT_STYLES: Record<string, string> = {
  accepted: 'bg-primary-container text-on-primary-container',
  rejected_tech: 'bg-secondary-container text-on-secondary-container',
  no_building: 'bg-surface-container-highest text-on-surface-variant',
  api_error: 'bg-surface-container-highest text-on-surface-variant',
  skipped_below_gate: 'bg-surface-container text-on-surface-variant opacity-70',
};

const VERDICT_LABELS: Record<string, string> = {
  accepted: 'Qualificato',
  rejected_tech: 'Rifiutato (tecnico)',
  no_building: 'Nessun edificio',
  api_error: 'Errore API',
  skipped_below_gate: 'Skip (gate)',
};

const VERDICT_ORDER: Record<string, number> = {
  accepted: 0,
  rejected_tech: 1,
  no_building: 2,
  api_error: 3,
  skipped_below_gate: 4,
};

type SortKey =
  | 'name'
  | 'ateco'
  | 'employees'
  | 'comune'
  | 'territorio'
  | 'stage'
  | 'score'
  | 'verdict'
  | 'created';

export function ContattiTable({ rows }: { rows: ContattoRow[] }) {
  const { sorted, sortKey, sortDir, requestSort } = useSortableData<
    ContattoRow,
    SortKey
  >(rows, (c, key) => {
    switch (key) {
      case 'name':
        return c.business_name ?? c.vat_number ?? '';
      case 'ateco':
        return c.ateco_code ?? '';
      case 'employees':
        return c.employees ?? null;
      case 'comune':
        return c.hq_city ?? '';
      case 'territorio':
        return c.territories?.name ?? '';
      case 'stage':
        return c.stage;
      case 'score':
        return c.score ?? null;
      case 'verdict':
        return c.solar_verdict ? VERDICT_ORDER[c.solar_verdict] ?? 99 : null;
      case 'created':
        return c.created_at;
    }
  });

  return (
    <div className="overflow-hidden rounded-lg bg-surface-container-low">
      <table className="w-full text-sm">
        <thead>
          <tr>
            <SortableTh sortKey="name" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Azienda</SortableTh>
            <SortableTh sortKey="ateco" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">ATECO</SortableTh>
            <SortableTh sortKey="employees" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3" align="right">Dipendenti</SortableTh>
            <SortableTh sortKey="comune" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Comune</SortableTh>
            <SortableTh sortKey="territorio" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Territorio</SortableTh>
            <SortableTh sortKey="stage" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3" align="center">Stadio</SortableTh>
            <SortableTh sortKey="score" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3" align="right">Score L3</SortableTh>
            <SortableTh sortKey="verdict" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Verdetto Solar</SortableTh>
            <SortableTh sortKey="created" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Scan</SortableTh>
          </tr>
        </thead>
        <tbody className="bg-surface-container-lowest">
          {sorted.map((c, idx) => (
            <tr
              key={c.id}
              className="transition-colors hover:bg-surface-container-low"
              style={
                idx !== 0
                  ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }
                  : undefined
              }
            >
              <td className="px-5 py-4 font-semibold text-on-surface">
                {c.business_name ?? (
                  <span className="font-mono text-xs text-on-surface-variant">
                    {c.vat_number ?? '—'}
                  </span>
                )}
              </td>
              <td className="px-5 py-4 font-mono text-xs text-on-surface-variant">
                {c.ateco_code ?? '—'}
              </td>
              <td className="px-5 py-4 text-right tabular-nums text-on-surface-variant">
                {c.employees ?? '—'}
              </td>
              <td className="px-5 py-4 text-on-surface-variant">
                {c.hq_city ?? '—'}{' '}
                {c.hq_province ? (
                  <span className="text-[10px] font-semibold uppercase opacity-60">
                    ({c.hq_province})
                  </span>
                ) : null}
              </td>
              <td className="px-5 py-4 text-xs text-on-surface-variant">
                {c.territories?.name ?? '—'}
              </td>
              <td className="px-5 py-4 text-center">
                <StageChip stage={c.stage} />
              </td>
              <td className="px-5 py-4 text-right font-headline font-bold tabular-nums">
                {c.score != null ? (
                  <span
                    className={cn(
                      c.score >= 70
                        ? 'text-primary'
                        : c.score >= 40
                          ? 'text-on-surface'
                          : 'text-on-surface-variant',
                    )}
                  >
                    {c.score}
                  </span>
                ) : (
                  <span className="text-on-surface-variant">—</span>
                )}
              </td>
              <td className="px-5 py-4">
                {c.solar_verdict ? (
                  <span
                    className={cn(
                      'inline-flex rounded-md px-2 py-0.5 text-xs font-medium',
                      VERDICT_STYLES[c.solar_verdict] ??
                        'bg-surface-container text-on-surface-variant',
                    )}
                  >
                    {VERDICT_LABELS[c.solar_verdict] ?? c.solar_verdict}
                  </span>
                ) : (
                  <span className="text-xs text-on-surface-variant">—</span>
                )}
              </td>
              <td className="px-5 py-4 text-xs text-on-surface-variant">
                {relativeTime(c.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StageChip({ stage }: { stage: number }) {
  const styles: Record<number, string> = {
    1: 'bg-surface-container-high text-on-surface-variant',
    2: 'bg-tertiary-container/60 text-on-tertiary-container',
    3: 'bg-tertiary-container text-on-tertiary-container',
    4: 'bg-primary-container text-on-primary-container',
  };
  return (
    <span
      className={cn(
        'inline-flex rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest',
        styles[stage] ?? 'bg-surface-container text-on-surface-variant',
      )}
    >
      L{stage}
    </span>
  );
}
