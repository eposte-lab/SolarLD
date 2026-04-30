/**
 * Practices index — tenant-scoped list of GSE practices.
 *
 * Sprint 2 additions:
 *   • "Scadenza" column: shows the nearest open deadline across all
 *     documents of the practice (computed client-side from the embedded
 *     sent_at timestamps now returned by the API).
 *   • Row-level badge: ⚠ Imminente (≤7 gg) or 🔴 Scaduto.
 *   • Filter: status select (unchanged) + new "Scadenze" quick-filter
 *     to show only rows with an open deadline.
 */
'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Clock, FolderOpen, Loader2, Plus } from 'lucide-react';

import { api, ApiError } from '@/lib/api-client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface EmbeddedDoc {
  id: string;
  template_code: string;
  status: string;
  pdf_url: string | null;
  sent_at: string | null;
  generated_at: string | null;
}

interface PracticeListItem {
  id: string;
  practice_number: string;
  practice_seq: number;
  status: string;
  impianto_potenza_kw: number;
  impianto_distributore: string;
  cliente_label: string;
  documenti_totali: number;
  documenti_pronti: number;
  created_at: string;
  updated_at: string;
  // The backend now includes sent_at + generated_at in the embedded docs.
  practice_documents?: EmbeddedDoc[];
}

// ---------------------------------------------------------------------------
// Deadline helpers
// ---------------------------------------------------------------------------

/** Calendar-day window within which the counterparty must respond. */
const DEADLINE_DAYS: Record<string, number | null> = {
  dm_37_08: null,
  comunicazione_comune: 30,
  modello_unico_p1: 42,
  modello_unico_p2: null,
  schema_unifilare: null,
  attestazione_titolo: null,
  tica_areti: 42,
  transizione_50_ex_ante: 60,
  transizione_50_ex_post: 60,
  transizione_50_attestazione: null,
};

interface DeadlineSummary {
  daysRemaining: number;
  dueDate: Date;
  isOverdue: boolean;
  isImminent: boolean;
  templateCode: string;
}

/**
 * Returns the *nearest* open deadline across all documents in a practice.
 * "Open" = status is `sent` + a regulatory window exists + not yet accepted.
 */
function nearestDeadline(docs: EmbeddedDoc[]): DeadlineSummary | null {
  let nearest: DeadlineSummary | null = null;
  for (const doc of docs) {
    if (doc.status !== 'sent' || !doc.sent_at) continue;
    const days = DEADLINE_DAYS[doc.template_code];
    if (!days) continue;
    const sent = new Date(doc.sent_at);
    const due = new Date(sent.getTime() + days * 86_400_000);
    const daysRemaining = Math.ceil((due.getTime() - Date.now()) / 86_400_000);
    const candidate: DeadlineSummary = {
      daysRemaining,
      dueDate: due,
      isOverdue: daysRemaining < 0,
      isImminent: daysRemaining >= 0 && daysRemaining <= 7,
      templateCode: doc.template_code,
    };
    if (
      !nearest ||
      candidate.daysRemaining < nearest.daysRemaining
    ) {
      nearest = candidate;
    }
  }
  return nearest;
}

// ---------------------------------------------------------------------------
// Lookup tables
// ---------------------------------------------------------------------------

const STATUS_LABELS: Record<string, string> = {
  in_preparation: 'In preparazione',
  documents_ready: 'Documenti pronti',
  documents_sent: 'Documenti inviati',
  in_progress: 'In corso',
  completed: 'Completata',
  blocked: 'Bloccata',
  cancelled: 'Annullata',
};

const STATUS_TONE: Record<string, string> = {
  in_preparation: 'bg-amber-100 text-amber-700',
  documents_ready: 'bg-blue-100 text-blue-700',
  documents_sent: 'bg-indigo-100 text-indigo-700',
  in_progress: 'bg-cyan-100 text-cyan-700',
  completed: 'bg-emerald-100 text-emerald-700',
  blocked: 'bg-rose-100 text-rose-700',
  cancelled: 'bg-zinc-200 text-zinc-600',
};

const DISTRIBUTORE_SHORT: Record<string, string> = {
  e_distribuzione: 'E-Distrib.',
  areti: 'Areti',
  unareti: 'Unareti',
  altro: 'Altro',
};

// ---------------------------------------------------------------------------

export default function PracticesIndexPage() {
  const [items, setItems] = useState<PracticeListItem[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [scadenzeOnly, setScadenzeOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const qs = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : '';
    api
      .get<PracticeListItem[]>(`/v1/practices${qs}`)
      .then((rows) => {
        if (!cancelled) setItems(rows);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg =
          err instanceof ApiError
            ? `Errore: ${err.message}`
            : 'Errore caricamento pratiche.';
        setError(msg);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [statusFilter]);

  // Annotate each item with its nearest deadline.
  const annotated = useMemo(
    () =>
      items.map((p) => ({
        ...p,
        deadline: nearestDeadline(p.practice_documents ?? []),
      })),
    [items],
  );

  // Apply the scadenze quick-filter.
  const visible = useMemo(
    () =>
      scadenzeOnly
        ? annotated.filter((p) => p.deadline !== null)
        : annotated,
    [annotated, scadenzeOnly],
  );

  // Counts for filter badges.
  const overdueCount = useMemo(
    () => annotated.filter((p) => p.deadline?.isOverdue).length,
    [annotated],
  );
  const imminentCount = useMemo(
    () =>
      annotated.filter((p) => !p.deadline?.isOverdue && p.deadline?.isImminent)
        .length,
    [annotated],
  );

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-on-surface">
            Pratiche GSE
          </h1>
          <p className="mt-1 text-sm text-on-surface-variant">
            Pratiche post-firma: DM 37/08, Comunicazione Comune, Modello
            Unico, TICA, Transizione 5.0.
          </p>
        </div>
        <Link
          href="/leads"
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-3.5 py-2 text-sm font-medium text-white hover:bg-primary/90"
        >
          <Plus size={16} />
          Crea da lead
        </Link>
      </header>

      {/* Filters bar */}
      <div className="flex flex-wrap items-center gap-3 rounded-xl bg-surface-container-lowest/60 p-3">
        <label className="text-sm text-on-surface-variant" htmlFor="status">
          Stato:
        </label>
        <select
          id="status"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-md border border-on-surface/10 bg-white px-3 py-1.5 text-sm"
        >
          <option value="">Tutte</option>
          {Object.entries(STATUS_LABELS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>

        {/* Scadenze quick-filter */}
        <button
          type="button"
          onClick={() => setScadenzeOnly((v) => !v)}
          className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors ${
            scadenzeOnly
              ? 'bg-amber-500 text-white'
              : 'border border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100'
          }`}
        >
          <Clock size={12} />
          Scadenze aperte
          {(overdueCount > 0 || imminentCount > 0) && (
            <span
              className={`ml-1 rounded-full px-1.5 py-0 text-[10px] font-bold ${
                scadenzeOnly ? 'bg-white/30' : 'bg-amber-500 text-white'
              }`}
            >
              {overdueCount + imminentCount}
            </span>
          )}
        </button>

        {overdueCount > 0 && (
          <span className="inline-flex items-center gap-1 text-xs text-rose-600">
            <AlertTriangle size={12} />
            {overdueCount} scadut{overdueCount === 1 ? 'a' : 'e'}
          </span>
        )}

        <span className="ml-auto text-xs text-on-surface-muted">
          {visible.length} pratiche
        </span>
      </div>

      {loading ? (
        <div className="flex items-center justify-center gap-2 py-16 text-on-surface-variant">
          <Loader2 size={18} className="animate-spin" />
          Caricamento…
        </div>
      ) : error ? (
        <div className="rounded-xl bg-rose-50 p-4 text-sm text-rose-700">
          {error}
        </div>
      ) : visible.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-xl bg-surface-container-lowest/60 px-6 py-16 text-center">
          <FolderOpen
            size={36}
            className="text-on-surface-muted"
            strokeWidth={1.4}
          />
          <h3 className="text-base font-semibold text-on-surface">
            {scadenzeOnly ? 'Nessuna scadenza aperta' : 'Nessuna pratica ancora'}
          </h3>
          <p className="max-w-md text-sm text-on-surface-variant">
            {scadenzeOnly
              ? 'Nessun documento inviato con scadenza regolamentare aperta.'
              : 'Le pratiche si creano dalla scheda lead, dopo aver impostato il feedback su contratto firmato.'}
          </p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl bg-white">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-on-surface/10 bg-surface-container-lowest/50 text-left text-xs uppercase tracking-wider text-on-surface-variant">
                <th className="px-4 py-3 font-semibold">Numero</th>
                <th className="px-4 py-3 font-semibold">Cliente</th>
                <th className="px-4 py-3 font-semibold">kWp</th>
                <th className="px-4 py-3 font-semibold">Distr.</th>
                <th className="px-4 py-3 font-semibold">Documenti</th>
                <th className="px-4 py-3 font-semibold">Stato</th>
                <th className="px-4 py-3 font-semibold">Scadenza</th>
                <th className="px-4 py-3 font-semibold">Aperta</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {visible.map((p) => (
                <PracticeRow key={p.id} item={p} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PracticeRow
// ---------------------------------------------------------------------------

function PracticeRow({
  item,
}: {
  item: PracticeListItem & { deadline: ReturnType<typeof nearestDeadline> };
}) {
  const { deadline } = item;

  return (
    <tr className="border-b border-on-surface/5 last:border-0 hover:bg-surface-container-lowest/40">
      <td className="px-4 py-3 font-mono text-xs">{item.practice_number}</td>
      <td className="max-w-[160px] truncate px-4 py-3">
        {item.cliente_label}
      </td>
      <td className="px-4 py-3">{item.impianto_potenza_kw.toFixed(2)}</td>
      <td className="px-4 py-3 text-xs">
        {DISTRIBUTORE_SHORT[item.impianto_distributore] ??
          item.impianto_distributore}
      </td>
      <td className="px-4 py-3 text-xs">
        <span
          className={
            item.documenti_pronti === item.documenti_totali
              ? 'text-emerald-700'
              : 'text-amber-700'
          }
        >
          {item.documenti_pronti}/{item.documenti_totali}
        </span>
      </td>
      <td className="px-4 py-3">
        <span
          className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ${
            STATUS_TONE[item.status] ?? 'bg-zinc-100 text-zinc-700'
          }`}
        >
          {STATUS_LABELS[item.status] ?? item.status}
        </span>
      </td>
      <td className="px-4 py-3">
        {deadline ? (
          deadline.isOverdue ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2 py-0.5 text-[10px] font-semibold text-rose-700">
              <AlertTriangle size={9} />
              Scaduta
            </span>
          ) : deadline.isImminent ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-700">
              <Clock size={9} />
              {deadline.daysRemaining} gg
            </span>
          ) : (
            <span className="text-xs text-on-surface-muted">
              {deadline.dueDate.toLocaleDateString('it-IT', {
                day: 'numeric',
                month: 'short',
              })}
            </span>
          )
        ) : (
          <span className="text-xs text-on-surface-muted">—</span>
        )}
      </td>
      <td className="px-4 py-3 text-xs text-on-surface-variant">
        {new Date(item.created_at).toLocaleDateString('it-IT')}
      </td>
      <td className="px-4 py-3">
        <Link href={`/practices/${item.id}`} className="text-primary hover:underline">
          Apri →
        </Link>
      </td>
    </tr>
  );
}
