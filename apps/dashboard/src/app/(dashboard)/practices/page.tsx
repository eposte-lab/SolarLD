/**
 * Practices index — tenant-scoped list of GSE practices.
 *
 * Restyled to match the Luminous Curator design system used by /leads:
 *   • Editorial header (small uppercase label + display h1) + GradientButton CTA.
 *   • Filters live in a BentoCard with FilterChip pills (no native <select>,
 *     no light bg-white surfaces clashing with the dark canvas).
 *   • Table also lives in a BentoCard — no more raw `bg-white` div.
 *
 * Sprint 2 deadline logic is unchanged: the nearest open deadline across
 * all sent documents drives the row badge (Imminente ≤7gg / Scaduta).
 */
'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Clock, FolderOpen, Loader2 } from 'lucide-react';

import { BentoCard } from '@/components/ui/bento-card';
import { GradientButton } from '@/components/ui/gradient-button';
import { api, ApiError } from '@/lib/api-client';
import { cn } from '@/lib/utils';

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

interface StatusOption {
  value: string;
  label: string;
}

const STATUS_OPTIONS: StatusOption[] = [
  { value: '', label: 'Tutte' },
  { value: 'in_preparation', label: 'In preparazione' },
  { value: 'documents_ready', label: 'Documenti pronti' },
  { value: 'documents_sent', label: 'Documenti inviati' },
  { value: 'in_progress', label: 'In corso' },
  { value: 'completed', label: 'Completata' },
  { value: 'blocked', label: 'Bloccata' },
  { value: 'cancelled', label: 'Annullata' },
];

const STATUS_LABELS: Record<string, string> = STATUS_OPTIONS.reduce(
  (acc, opt) => {
    if (opt.value) acc[opt.value] = opt.label;
    return acc;
  },
  {} as Record<string, string>,
);

const STATUS_TONE: Record<string, string> = {
  in_preparation: 'bg-amber-500/15 text-amber-300',
  documents_ready: 'bg-blue-500/15 text-blue-300',
  documents_sent: 'bg-indigo-500/15 text-indigo-300',
  in_progress: 'bg-cyan-500/15 text-cyan-300',
  completed: 'bg-emerald-500/15 text-emerald-300',
  blocked: 'bg-rose-500/15 text-rose-300',
  cancelled: 'bg-zinc-500/20 text-zinc-300',
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
  const deadlineBadgeCount = overdueCount + imminentCount;

  return (
    <div className="space-y-6">
      {/* Header ------------------------------------------------------- */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Pratiche post-firma · {visible.length.toLocaleString('it-IT')}
          </p>
          <h1 className="font-headline text-4xl font-bold tracking-tighter text-on-surface">
            Pratiche GSE
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-on-surface-variant">
            DM 37/08, Comunicazione Comune, Modello Unico, TICA, Transizione 5.0.
          </p>
        </div>
        <GradientButton href="/leads" size="sm" variant="secondary">
          Crea da lead
        </GradientButton>
      </header>

      {/* Filters ------------------------------------------------------ */}
      <BentoCard padding="tight" span="full">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3 px-2 py-2">
          <FilterGroup label="Stato">
            {STATUS_OPTIONS.map((opt) => (
              <FilterChip
                key={opt.value || 'all'}
                active={statusFilter === opt.value}
                onClick={() => setStatusFilter(opt.value)}
              >
                {opt.label}
              </FilterChip>
            ))}
          </FilterGroup>

          <FilterGroup label="Vista">
            <button
              type="button"
              onClick={() => setScadenzeOnly((v) => !v)}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold transition-colors',
                scadenzeOnly
                  ? 'bg-amber-500 text-zinc-900 shadow-ambient-sm'
                  : 'bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface',
              )}
            >
              <Clock size={12} />
              Scadenze aperte
              {deadlineBadgeCount > 0 && (
                <span
                  className={cn(
                    'ml-0.5 rounded-full px-1.5 py-0 text-[10px] font-bold',
                    scadenzeOnly
                      ? 'bg-zinc-900/15 text-zinc-900'
                      : 'bg-amber-500/20 text-amber-300',
                  )}
                >
                  {deadlineBadgeCount}
                </span>
              )}
            </button>
            {overdueCount > 0 && (
              <span className="inline-flex items-center gap-1 text-[11px] font-medium text-rose-400">
                <AlertTriangle size={12} />
                {overdueCount} scadut{overdueCount === 1 ? 'a' : 'e'}
              </span>
            )}
          </FilterGroup>
        </div>
      </BentoCard>

      {/* Table -------------------------------------------------------- */}
      <BentoCard padding="tight" span="full">
        {loading ? (
          <div className="flex items-center justify-center gap-2 py-20 text-sm text-on-surface-variant">
            <Loader2 size={18} className="animate-spin" />
            Caricamento…
          </div>
        ) : error ? (
          <div className="rounded-lg bg-rose-500/10 p-6 text-sm text-rose-300">
            {error}
          </div>
        ) : visible.length === 0 ? (
          <div className="flex flex-col items-center gap-3 rounded-lg bg-surface-container-low px-6 py-16 text-center">
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
          <div className="overflow-hidden rounded-lg">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-on-surface/10 text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  <th className="px-4 py-3">Numero</th>
                  <th className="px-4 py-3">Cliente</th>
                  <th className="px-4 py-3">kWp</th>
                  <th className="px-4 py-3">Distr.</th>
                  <th className="px-4 py-3">Documenti</th>
                  <th className="px-4 py-3">Stato</th>
                  <th className="px-4 py-3">Scadenza</th>
                  <th className="px-4 py-3">Aperta</th>
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
      </BentoCard>
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
    <tr className="border-b border-on-surface/5 last:border-0 transition-colors hover:bg-surface-container-low">
      <td className="px-4 py-3 font-mono text-xs text-on-surface">
        {item.practice_number}
      </td>
      <td className="max-w-[180px] truncate px-4 py-3 text-on-surface">
        {item.cliente_label}
      </td>
      <td className="px-4 py-3 text-on-surface">
        {item.impianto_potenza_kw.toFixed(2)}
      </td>
      <td className="px-4 py-3 text-xs text-on-surface-variant">
        {DISTRIBUTORE_SHORT[item.impianto_distributore] ??
          item.impianto_distributore}
      </td>
      <td className="px-4 py-3 text-xs">
        <span
          className={
            item.documenti_pronti === item.documenti_totali
              ? 'text-emerald-300'
              : 'text-amber-300'
          }
        >
          {item.documenti_pronti}/{item.documenti_totali}
        </span>
      </td>
      <td className="px-4 py-3">
        <span
          className={cn(
            'inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium',
            STATUS_TONE[item.status] ?? 'bg-zinc-500/20 text-zinc-300',
          )}
        >
          {STATUS_LABELS[item.status] ?? item.status}
        </span>
      </td>
      <td className="px-4 py-3">
        {deadline ? (
          deadline.isOverdue ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-rose-500/15 px-2 py-0.5 text-[10px] font-semibold text-rose-300">
              <AlertTriangle size={9} />
              Scaduta
            </span>
          ) : deadline.isImminent ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold text-amber-300">
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
        <Link
          href={`/practices/${item.id}`}
          className="text-xs font-semibold text-primary hover:underline"
        >
          Apri →
        </Link>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Filter UI (local components — same shape as /leads page)
// ---------------------------------------------------------------------------

function FilterGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
        {label}
      </span>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-full px-3 py-1 text-xs font-semibold transition-colors',
        active
          ? 'bg-primary text-on-primary shadow-ambient-sm'
          : 'bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface',
      )}
    >
      {children}
    </button>
  );
}
