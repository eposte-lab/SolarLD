'use client';

/**
 * TerritoryTable — client wrapper for the territories list with sortable
 * column headers. Server actions (`triggerScan`, `deleteTerritory`) are
 * imported directly because they're declared in a `'use server'` module
 * — Next.js bridges the call through a fetch.
 */

import { AlertTriangle, Check } from 'lucide-react';

import { SortableTh } from '@/components/ui/sortable-th';
import { useSortableData } from '@/hooks/use-sortable-data';
import { cn } from '@/lib/utils';
import type { ScanSummary } from '@/lib/data/territories';
import type { TerritoryRow, TerritoryType } from '@/types/db';

import { deleteTerritory, triggerScan } from '@/app/(dashboard)/territories/_actions';

const TYPE_LABEL: Record<TerritoryType, string> = {
  cap: 'CAP',
  comune: 'Comune',
  provincia: 'Provincia',
  regione: 'Regione',
};

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('it-IT', {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
    });
  } catch {
    return iso;
  }
}

type SortKey =
  | 'name'
  | 'type'
  | 'code'
  | 'priority'
  | 'status'
  | 'last_scan'
  | 'created';

function statusKey(t: TerritoryRow): number {
  // 0 = primary (high priority), 1 = neutral (active), 2 = excluded (sorted last)
  if (t.excluded) return 2;
  if (t.priority >= 7) return 0;
  return 1;
}

export function TerritoryTable({
  rows,
  scanSummaries,
  isLocked = false,
}: {
  rows: TerritoryRow[];
  scanSummaries: Map<string, ScanSummary>;
  isLocked?: boolean;
}) {
  const { sorted, sortKey, sortDir, requestSort } = useSortableData<
    TerritoryRow,
    SortKey
  >(rows, (t, key) => {
    switch (key) {
      case 'name':
        return t.name;
      case 'type':
        return TYPE_LABEL[t.type];
      case 'code':
        return t.code;
      case 'priority':
        return t.priority;
      case 'status':
        return statusKey(t);
      case 'last_scan':
        return scanSummaries.get(t.id)?.occurred_at ?? null;
      case 'created':
        return t.created_at;
    }
  });

  return (
    <div className="overflow-hidden rounded-lg bg-surface-container-low">
      <table className="w-full text-sm">
        <thead>
          <tr>
            <SortableTh sortKey="name" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Nome</SortableTh>
            <SortableTh sortKey="type" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Tipo</SortableTh>
            <SortableTh sortKey="code" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Codice</SortableTh>
            <SortableTh sortKey="priority" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3" align="right">Priorità</SortableTh>
            <th className="px-5 py-3 text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">Bbox</th>
            <SortableTh sortKey="status" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Stato</SortableTh>
            <SortableTh sortKey="last_scan" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Ultima scan</SortableTh>
            <SortableTh sortKey="created" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Aggiunto</SortableTh>
            <th className="px-5 py-3 text-right" />
          </tr>
        </thead>
        <tbody className="bg-surface-container-lowest">
          {sorted.map((t, idx) => (
            <tr
              key={t.id}
              className="transition-colors hover:bg-surface-container-low"
              style={
                idx !== 0
                  ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }
                  : undefined
              }
            >
              <td className="px-5 py-4 font-semibold text-on-surface">
                {t.name}
              </td>
              <td className="px-5 py-4 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                {TYPE_LABEL[t.type]}
              </td>
              <td className="px-5 py-4 font-mono text-xs text-on-surface">
                {t.code}
              </td>
              <td className="px-5 py-4 text-right font-headline font-bold tabular-nums">
                {t.priority}
              </td>
              <td className="px-5 py-4">
                {t.bbox ? (
                  <BboxPreview bbox={t.bbox} />
                ) : (
                  <span className="text-[10px] font-semibold uppercase tracking-wide text-secondary">
                    Mancante
                  </span>
                )}
              </td>
              <td className="px-5 py-4">
                {t.excluded ? (
                  <Badge tone="muted">Escluso</Badge>
                ) : t.priority >= 7 ? (
                  <Badge tone="primary">Priorità alta</Badge>
                ) : (
                  <Badge tone="neutral">Attivo</Badge>
                )}
              </td>
              <td className="px-5 py-4">
                <LastScanBadge summary={scanSummaries.get(t.id)} />
              </td>
              <td className="px-5 py-4 text-xs text-on-surface-variant">
                {formatDate(t.created_at)}
              </td>
              <td className="px-5 py-4">
                <div className="flex items-center justify-end gap-3">
                  <ScanButton id={t.id} name={t.name} hasBbox={Boolean(t.bbox)} />
                  {!isLocked && <DeleteButton id={t.id} name={t.name} />}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BboxPreview({
  bbox,
}: {
  bbox: NonNullable<TerritoryRow['bbox']>;
}) {
  return (
    <span className="font-mono text-[10px] text-on-surface-variant">
      {bbox.ne.lat.toFixed(3)},{bbox.ne.lng.toFixed(3)}
      <br />
      {bbox.sw.lat.toFixed(3)},{bbox.sw.lng.toFixed(3)}
    </span>
  );
}

function ScanButton({
  id,
  name,
  hasBbox,
}: {
  id: string;
  name: string;
  hasBbox: boolean;
}) {
  return (
    <form action={triggerScan} className="inline">
      <input type="hidden" name="id" value={id} />
      <input type="hidden" name="has_bbox" value={hasBbox ? '1' : '0'} />
      <button
        type="submit"
        disabled={!hasBbox}
        title={
          hasBbox
            ? `Avvia scansione tetti per ${name}`
            : 'Bbox mancante — elimina e ri-aggiungi il territorio con le coordinate'
        }
        className={cn(
          'rounded-full px-3 py-1 text-xs font-semibold transition-colors',
          hasBbox
            ? 'bg-primary-container text-on-primary-container hover:bg-primary/20'
            : 'cursor-not-allowed bg-surface-container text-on-surface-variant opacity-50',
        )}
        aria-label={`Avvia scansione ${name}`}
      >
        Scansiona
      </button>
    </form>
  );
}

function DeleteButton({ id, name }: { id: string; name: string }) {
  return (
    <form action={deleteTerritory} className="inline">
      <input type="hidden" name="id" value={id} />
      <button
        type="submit"
        className="text-xs font-semibold text-secondary hover:underline"
        aria-label={`Elimina ${name}`}
      >
        elimina
      </button>
    </form>
  );
}

const BADGE_TONE = {
  primary: 'bg-primary-container text-on-primary-container',
  neutral: 'bg-surface-container-high text-on-surface',
  muted: 'bg-surface-container text-on-surface-variant',
} as const;

function Badge({
  tone,
  children,
}: {
  tone: keyof typeof BADGE_TONE;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-widest',
        BADGE_TONE[tone],
      )}
    >
      {children}
    </span>
  );
}

function LastScanBadge({ summary }: { summary?: ScanSummary }) {
  if (!summary) {
    return (
      <span className="text-[10px] text-on-surface-variant/50">
        Mai eseguita
      </span>
    );
  }

  const date = new Date(summary.occurred_at).toLocaleDateString('it-IT', {
    day: '2-digit',
    month: 'short',
  });

  if (summary.atoka_empty) {
    return (
      <span
        className="inline-flex flex-col gap-0.5"
        title="Nessuna azienda trovata — possibile problema di configurazione del servizio di scoperta"
      >
        <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-error">
          <AlertTriangle size={10} strokeWidth={2.5} aria-hidden />
          0 aziende trovate
        </span>
        <span className="text-[9px] text-on-surface-variant">{date}</span>
      </span>
    );
  }

  if (summary.leads_qualified === 0) {
    return (
      <span className="inline-flex flex-col gap-0.5">
        <span className="text-[10px] font-semibold text-on-surface-variant">
          0 lead
        </span>
        <span className="text-[9px] text-on-surface-variant">{date}</span>
      </span>
    );
  }

  return (
    <span className="inline-flex flex-col gap-0.5">
      <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-primary">
        <Check size={10} strokeWidth={2.75} aria-hidden />
        {summary.leads_qualified} lead
      </span>
      <span className="text-[9px] text-on-surface-variant">{date}</span>
    </span>
  );
}
