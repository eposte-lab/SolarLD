/**
 * Practice detail — single GSE practice with its document cards.
 *
 * Why client component: the document grid polls every 3 s while at
 * least one row is still in `draft` without `pdf_url` (worker
 * pending). We also do optimistic state updates for the regenerate
 * and "mark as sent" buttons.
 *
 * Sprint 2 additions:
 *   • DocTimeline — horizontal step-indicator showing the state machine
 *     (draft → reviewed → sent → accepted/rejected → completed).
 *   • DeadlineTag — shows "Scadenza gg-mmm" / "⚠ Imminente" / "Scaduto"
 *     when a document is in `sent` state and the regulator's reply window
 *     is open (TICA 30 gg lav., Comune 30 gg, T5.0 60 gg, etc.).
 *   • ScadenzeSection — summary panel below "Dati pratica" listing all
 *     upcoming or overdue deadlines in one place.
 */
'use client';

import Link from 'next/link';
import { use, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Clock,
  Download,
  FileText,
  Loader2,
  RefreshCw,
  Send,
  X,
} from 'lucide-react';

import { api, ApiError, API_URL } from '@/lib/api-client';
import { PracticeUploadsPanel } from '@/components/practices/practice-uploads-panel';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DocumentRow {
  id: string;
  practice_id: string;
  template_code: string;
  template_version: string;
  status: string;
  pdf_url: string | null;
  generation_error: string | null;
  generated_at: string | null;
  sent_at: string | null;
  accepted_at: string | null;
  rejected_at: string | null;
  rejection_reason: string | null;
  created_at: string;
  updated_at: string;
}

// Missing-fields report types (from GET /v1/practices/{id}/missing-fields)
interface MissingFieldItem {
  path: string;
  label: string;
  source: 'tenant' | 'practice' | 'extras' | 'subject';
  api_field: string | null;
}

interface MissingFieldsReport {
  all_ready: boolean;
  templates: Array<{
    template_code: string;
    ready: boolean;
    missing: MissingFieldItem[];
  }>;
  by_source: {
    tenant: MissingFieldItem[];
    practice: MissingFieldItem[];
    subject: MissingFieldItem[];
  };
}

interface PracticeEvent {
  id: string;
  practice_id: string;
  document_id: string | null;
  event_type: string;
  payload: Record<string, unknown>;
  actor_user_id: string | null;
  occurred_at: string;
  created_at: string;
}

interface PracticeDeadline {
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
}

interface PracticeDetail {
  id: string;
  practice_number: string;
  status: string;
  quote_id: string | null;
  impianto_potenza_kw: number;
  impianto_pannelli_count: number | null;
  impianto_pod: string | null;
  impianto_distributore: string;
  impianto_data_inizio_lavori: string | null;
  impianto_data_fine_lavori: string | null;
  catastale_foglio: string | null;
  catastale_particella: string | null;
  catastale_subalterno: string | null;
  componenti_data: Record<string, unknown>;
  extras: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  leads?: {
    id: string;
    subjects?: {
      business_name: string | null;
      owner_first_name: string | null;
      owner_last_name: string | null;
    } | null;
  } | null;
  practice_documents?: DocumentRow[];
}

// ---------------------------------------------------------------------------
// Lookup tables
// ---------------------------------------------------------------------------

const TEMPLATE_LABELS: Record<string, string> = {
  dm_37_08: 'DM 37/08 — Dichiarazione di conformità',
  comunicazione_comune: 'Comunicazione al Comune (fine lavori)',
  modello_unico_p1: 'Modello Unico — Parte I (pre-lavori)',
  modello_unico_p2: 'Modello Unico — Parte II (as-built)',
  schema_unifilare: 'Schema elettrico unifilare (CEI 0-21)',
  attestazione_titolo: 'Modulo ATR — Attestazione titolo',
  tica_areti: 'Istanza TICA — Allegato 1 ARERA 109/2021',
  transizione_50_ex_ante: 'Transizione 5.0 — Allegato VIII (ex-ante)',
  transizione_50_ex_post: 'Transizione 5.0 — Allegato X (ex-post)',
  transizione_50_attestazione: 'Transizione 5.0 — Allegato V',
};

const TEMPLATE_DESCRIPTIONS: Record<string, string> = {
  dm_37_08:
    "Dichiarazione di conformità impianto elettrico a regola d'arte (DM 22/01/2008 n. 37).",
  comunicazione_comune:
    'Comunicazione di fine lavori al Comune (DPR 380/2001 art. 6).',
  modello_unico_p1:
    'Istanza pre-lavori al gestore di rete (D.Lgs. 199/2021 art. 25).',
  modello_unico_p2:
    'Comunicazione as-built con codice identificativo connessione.',
  schema_unifilare:
    'Schema elettrico unifilare allegato obbligatorio al MU e TICA.',
  attestazione_titolo:
    'Modulo ATR — attesta il titolo a richiedere la connessione (IRETI/Unareti).',
  tica_areti:
    "Istanza di accesso TICA per Areti S.p.A. (Roma) — Allegato 1 delibera 109/2021.",
  transizione_50_ex_ante:
    'Certificazione pre-investimento (Allegato VIII DM T5.0).',
  transizione_50_ex_post:
    'Certificazione post-realizzazione (Allegato X DM T5.0).',
  transizione_50_attestazione:
    'Attestazione possesso perizia tecnica + certificazione contabile (Allegato V).',
};

/** Calendar-day deadline after `sent_at` for regulatory responses.
 *  null = no awaited response (our own declaration / allegato, no reply expected).
 *  Approx: 30 gg lavorativi ≈ 42 gg calendario (×1.4). */
const DEADLINE_CALENDAR_DAYS: Record<string, number | null> = {
  dm_37_08: null,
  comunicazione_comune: 30,      // DPR 380/2001 — 30 gg risposta Comune
  modello_unico_p1: 42,          // ARERA: 30 gg lav. preventivo connessione
  modello_unico_p2: null,        // fine lavori — non attende risposta
  schema_unifilare: null,
  attestazione_titolo: null,
  tica_areti: 42,                // ARERA 109/2021: 30 gg lav. preventivo
  transizione_50_ex_ante: 60,    // GSE: 60 gg per validazione
  transizione_50_ex_post: 60,
  transizione_50_attestazione: null,
};

const DOC_STATUS_LABELS: Record<string, string> = {
  draft: 'Bozza',
  reviewed: 'Verificato',
  sent: 'Inviato',
  accepted: 'Accettato',
  rejected: 'Respinto',
  amended: 'Integrato',
  completed: 'Completato',
};

const DOC_STATUS_TONE: Record<string, string> = {
  draft: 'bg-amber-100 text-amber-700',
  reviewed: 'bg-blue-100 text-blue-700',
  sent: 'bg-indigo-100 text-indigo-700',
  accepted: 'bg-emerald-100 text-emerald-700',
  rejected: 'bg-rose-100 text-rose-700',
  amended: 'bg-cyan-100 text-cyan-700',
  completed: 'bg-emerald-100 text-emerald-700',
};

const PRACTICE_STATUS_LABELS: Record<string, string> = {
  in_preparation: 'In preparazione',
  documents_ready: 'Documenti pronti',
  documents_sent: 'Documenti inviati',
  in_progress: 'In corso',
  completed: 'Completata',
  blocked: 'Bloccata',
  cancelled: 'Annullata',
};

const DISTRIBUTORE_LABELS: Record<string, string> = {
  e_distribuzione: 'E-Distribuzione',
  areti: 'Areti (Roma)',
  unareti: 'Unareti (Milano)',
  altro: 'Altro',
};

/** State machine for documents: the ordered list of steps. */
const DOC_TIMELINE_STEPS = [
  { key: 'draft', label: 'Bozza' },
  { key: 'reviewed', label: 'Verificato' },
  { key: 'sent', label: 'Inviato' },
  { key: 'accepted', label: 'Accettato' },
  { key: 'completed', label: 'Completato' },
] as const;

const REJECTED_STEPS = [
  { key: 'draft', label: 'Bozza' },
  { key: 'reviewed', label: 'Verificato' },
  { key: 'sent', label: 'Inviato' },
  { key: 'rejected', label: 'Respinto' },
  { key: 'amended', label: 'Integrato' },
] as const;

// ---------------------------------------------------------------------------
// Deadline helpers (pure, no side-effects)
// ---------------------------------------------------------------------------

interface DeadlineInfo {
  dueDate: Date;
  daysRemaining: number; // negative = overdue
  isOverdue: boolean;
  isImminent: boolean; // ≤7 days
}

function computeDeadline(
  templateCode: string,
  sentAt: string | null,
): DeadlineInfo | null {
  const days = DEADLINE_CALENDAR_DAYS[templateCode];
  if (!days || !sentAt) return null;
  const sent = new Date(sentAt);
  const due = new Date(sent.getTime() + days * 86_400_000);
  const now = Date.now();
  const daysRemaining = Math.ceil((due.getTime() - now) / 86_400_000);
  return {
    dueDate: due,
    daysRemaining,
    isOverdue: daysRemaining < 0,
    isImminent: daysRemaining >= 0 && daysRemaining <= 7,
  };
}

// ---------------------------------------------------------------------------

export default function PracticeDetailPage({
  params,
}: {
  // Next.js 15 ships `params` as a Promise — unwrap with React.use() in
  // a client component (cf. quote/page.tsx, practice/new/page.tsx).
  params: Promise<{ id: string }>;
}) {
  const { id: practiceId } = use(params);

  const [data, setData] = useState<PracticeDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [previewCode, setPreviewCode] = useState<string | null>(null);
  const [actionFor, setActionFor] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [events, setEvents] = useState<PracticeEvent[]>([]);
  const [serverDeadlines, setServerDeadlines] = useState<PracticeDeadline[]>([]);
  const [missingReport, setMissingReport] = useState<MissingFieldsReport | null>(null);

  const refetch = useCallback(async () => {
    try {
      // Detail + events + deadlines in parallel — they don't depend
      // on each other and the events / deadlines lists are tiny.
      const [detailRes, eventsRes, deadlinesRes, missingRes] = await Promise.all([
        api.get<PracticeDetail>(`/v1/practices/${practiceId}`),
        api
          .get<PracticeEvent[]>(`/v1/practices/${practiceId}/events`)
          .catch(() => [] as PracticeEvent[]),
        api
          .get<PracticeDeadline[]>(`/v1/practices/${practiceId}/deadlines`)
          .catch(() => [] as PracticeDeadline[]),
        api
          .get<MissingFieldsReport>(`/v1/practices/${practiceId}/missing-fields`)
          .catch(() => null),
      ]);
      setData(detailRes);
      setEvents(eventsRes);
      setServerDeadlines(deadlinesRes);
      setMissingReport(missingRes);
      setError(null);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.message : 'Errore caricamento pratica.';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [practiceId]);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  // Poll while any document is still in flight.
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const isPending = useMemo(() => {
    const docs = data?.practice_documents ?? [];
    return docs.some(
      (d) => !d.pdf_url && !d.generation_error && d.status === 'draft',
    );
  }, [data]);

  useEffect(() => {
    if (!isPending) {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
      return;
    }
    pollingRef.current = setInterval(() => {
      void refetch();
    }, 3000);
    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [isPending, refetch]);

  // Auto-select first ready document for preview.
  useEffect(() => {
    if (previewCode) return;
    const ready = (data?.practice_documents ?? []).find((d) => d.pdf_url);
    if (ready) setPreviewCode(ready.template_code);
  }, [data, previewCode]);

  const cliente = useMemo(() => {
    const subj = data?.leads?.subjects;
    if (!subj) return '—';
    return (
      subj.business_name ||
      [subj.owner_first_name, subj.owner_last_name].filter(Boolean).join(' ') ||
      '—'
    );
  }, [data]);

  // Compute deadline info for each document that is in `sent` state.
  const deadlines = useMemo(() => {
    const docs = data?.practice_documents ?? [];
    return docs
      .map((d) => ({
        doc: d,
        deadline:
          d.status === 'sent'
            ? computeDeadline(d.template_code, d.sent_at)
            : null,
      }))
      .filter((x) => x.deadline !== null) as Array<{
      doc: DocumentRow;
      deadline: DeadlineInfo;
    }>;
  }, [data]);

  async function handleRegenerate(code: string) {
    setActionFor(`regenerate:${code}`);
    setActionError(null);
    try {
      await api.post(
        `/v1/practices/${practiceId}/documents/${code}/regenerate`,
        {},
      );
      await refetch();
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : 'Rigenerazione fallita.',
      );
    } finally {
      setActionFor(null);
    }
  }

  async function handleMarkSent(code: string) {
    setActionFor(`sent:${code}`);
    setActionError(null);
    try {
      await api.patch(`/v1/practices/${practiceId}/documents/${code}`, {
        status: 'sent',
      });
      await refetch();
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : 'Aggiornamento fallito.',
      );
    } finally {
      setActionFor(null);
    }
  }

  // ----- Render --------------------------------------------------------------

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-12 text-sm text-on-surface-variant">
        <Loader2 size={16} className="animate-spin" /> Caricamento pratica…
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="space-y-3">
        <Link
          href="/practices"
          className="inline-flex items-center gap-1 text-xs text-on-surface-variant hover:text-primary"
        >
          <ArrowLeft size={12} /> Torna all&apos;elenco
        </Link>
        <div className="rounded-xl bg-rose-50 p-4 text-sm text-rose-700">
          {error ?? 'Pratica non trovata.'}
        </div>
      </div>
    );
  }

  const documents = data.practice_documents ?? [];
  const previewDoc = previewCode
    ? documents.find((d) => d.template_code === previewCode) ?? null
    : null;

  return (
    <div className="space-y-6">
      <Link
        href="/practices"
        className="inline-flex items-center gap-1 text-xs font-medium text-on-surface-variant hover:text-primary"
      >
        <ArrowLeft size={12} strokeWidth={2.25} /> Torna all&apos;elenco pratiche
      </Link>

      {/* Header */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
            Pratica GSE
          </p>
          <h1 className="font-headline text-3xl font-bold tracking-tighter text-on-surface">
            {data.practice_number}
          </h1>
          <p className="mt-1 text-sm text-on-surface-variant">
            Cliente: <strong>{cliente}</strong> ·{' '}
            {data.impianto_potenza_kw.toFixed(2)} kWp ·{' '}
            {DISTRIBUTORE_LABELS[data.impianto_distributore] ??
              data.impianto_distributore}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex rounded-full px-3 py-1 text-xs font-medium ${
              data.status === 'completed'
                ? 'bg-emerald-100 text-emerald-700'
                : data.status === 'blocked' || data.status === 'cancelled'
                  ? 'bg-rose-100 text-rose-700'
                  : 'bg-blue-100 text-blue-700'
            }`}
          >
            {PRACTICE_STATUS_LABELS[data.status] ?? data.status}
          </span>
          {data.leads?.id && (
            <Link
              href={`/leads/${data.leads.id}`}
              className="rounded-lg border border-on-surface/10 px-3 py-1.5 text-xs font-medium text-on-surface-variant hover:bg-surface-container-lowest/40"
            >
              Apri lead
            </Link>
          )}
        </div>
      </header>

      {actionError && (
        <div className="rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {actionError}
        </div>
      )}

      {/* Scadenze banner — only when at least one deadline is live */}
      {deadlines.length > 0 && (
        <ScadenzeSection deadlines={deadlines} />
      )}

      {/* Missing data panel — shown when any template has unresolvable fields */}
      {missingReport && !missingReport.all_ready && (
        <MissingDataPanel
          practiceId={practiceId}
          report={missingReport}
          leadId={data.leads?.id ?? null}
          documents={documents}
          onSaved={() => {
            void refetch();
          }}
        />
      )}

      {/* OCR uploads — drag-drop customer documents (visura, ID, bolletta).
          Claude Vision extracts fields, operator clicks "Applica" to fan
          values into tenant/subject/practice and the missing-fields panel
          updates after the parent refetch. */}
      <PracticeUploadsPanel
        practiceId={practiceId}
        onAfterApply={() => {
          void refetch();
        }}
      />

      {/* Documents */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-widest text-on-surface-variant">
          Documenti
        </h2>
        {documents.length === 0 ? (
          <div className="rounded-xl bg-surface-container-lowest/60 p-6 text-sm text-on-surface-variant">
            Nessun documento associato a questa pratica.
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {documents.map((doc) => (
              <DocumentCard
                key={doc.id}
                doc={doc}
                practiceId={practiceId}
                isPreview={previewCode === doc.template_code}
                isBusy={actionFor?.endsWith(`:${doc.template_code}`) ?? false}
                onPreview={() => setPreviewCode(doc.template_code)}
                onRegenerate={() => handleRegenerate(doc.template_code)}
                onMarkSent={() => handleMarkSent(doc.template_code)}
              />
            ))}
          </div>
        )}
      </section>

      {/* Practice data — readonly */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-widest text-on-surface-variant">
          Dati pratica
        </h2>
        <div className="grid gap-3 rounded-xl bg-white p-5 md:grid-cols-3">
          <DataRow label="POD" value={data.impianto_pod} />
          <DataRow
            label="Distributore"
            value={
              DISTRIBUTORE_LABELS[data.impianto_distributore] ??
              data.impianto_distributore
            }
          />
          <DataRow
            label="Potenza impianto"
            value={`${data.impianto_potenza_kw.toFixed(2)} kWp`}
          />
          <DataRow
            label="Numero pannelli"
            value={
              data.impianto_pannelli_count != null
                ? String(data.impianto_pannelli_count)
                : null
            }
          />
          <DataRow
            label="Inizio lavori"
            value={formatDate(data.impianto_data_inizio_lavori)}
          />
          <DataRow
            label="Fine lavori"
            value={formatDate(data.impianto_data_fine_lavori)}
          />
          <DataRow label="Foglio catastale" value={data.catastale_foglio} />
          <DataRow label="Particella" value={data.catastale_particella} />
          <DataRow label="Subalterno" value={data.catastale_subalterno} />
        </div>
      </section>

      {/* Server-side deadlines (Livello 2) — explicit rule-driven SLAs
          recorded in practice_deadlines.  Sits next to ScadenzeSection
          which is the client-side computed view; the two complement
          each other (server picks up T5.0 ex-post, MU p2 due, etc.). */}
      {serverDeadlines.length > 0 && (
        <ServerDeadlinesPanel deadlines={serverDeadlines} />
      )}

      {/* Event timeline — append-only audit log from practice_events */}
      {events.length > 0 && <EventTimelinePanel events={events} />}

      {/* PDF preview pane */}
      {previewDoc && previewDoc.pdf_url && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold uppercase tracking-widest text-on-surface-variant">
            Anteprima —{' '}
            {TEMPLATE_LABELS[previewDoc.template_code] ??
              previewDoc.template_code}
          </h2>
          <iframe
            key={previewDoc.id}
            src={`${API_URL}/v1/practices/${practiceId}/documents/${previewDoc.template_code}/download`}
            className="h-[800px] w-full rounded-lg border border-on-surface/10 bg-white"
            title={`Anteprima ${previewDoc.template_code}`}
          />
        </section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ScadenzeSection
// ---------------------------------------------------------------------------

function ScadenzeSection({
  deadlines,
}: {
  deadlines: Array<{ doc: DocumentRow; deadline: DeadlineInfo }>;
}) {
  const hasOverdue = deadlines.some((d) => d.deadline.isOverdue);
  const hasImminent = deadlines.some((d) => d.deadline.isImminent);

  return (
    <section
      className={`rounded-xl border p-4 ${
        hasOverdue
          ? 'border-rose-200 bg-rose-50'
          : hasImminent
            ? 'border-amber-200 bg-amber-50'
            : 'border-blue-200 bg-blue-50'
      }`}
    >
      <div className="mb-3 flex items-center gap-2">
        <Clock
          size={16}
          className={
            hasOverdue
              ? 'text-rose-600'
              : hasImminent
                ? 'text-amber-600'
                : 'text-blue-600'
          }
        />
        <h2
          className={`text-sm font-semibold ${
            hasOverdue
              ? 'text-rose-800'
              : hasImminent
                ? 'text-amber-800'
                : 'text-blue-800'
          }`}
        >
          Scadenze regolamentari
        </h2>
      </div>
      <div className="space-y-2">
        {deadlines.map(({ doc, deadline }) => (
          <div
            key={doc.id}
            className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-white/70 px-3 py-2"
          >
            <span className="text-xs font-medium text-on-surface">
              {TEMPLATE_LABELS[doc.template_code] ?? doc.template_code}
            </span>
            <div className="flex items-center gap-2">
              <span className="text-xs text-on-surface-variant">
                Inviato {formatDate(doc.sent_at)} · Scade{' '}
                {deadline.dueDate.toLocaleDateString('it-IT')}
              </span>
              <DeadlineChip deadline={deadline} />
            </div>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-on-surface-muted">
        Termini indicativi da normativa ARERA / DPR 380/2001. Verificare
        le comunicazioni ufficiali del distributore / ente competente.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------
// DocumentCard
// ---------------------------------------------------------------------------

function DocumentCard({
  doc,
  practiceId,
  isPreview,
  isBusy,
  onPreview,
  onRegenerate,
  onMarkSent,
}: {
  doc: DocumentRow;
  practiceId: string;
  isPreview: boolean;
  isBusy: boolean;
  onPreview: () => void;
  onRegenerate: () => void;
  onMarkSent: () => void;
}) {
  const label = TEMPLATE_LABELS[doc.template_code] ?? doc.template_code;
  const description = TEMPLATE_DESCRIPTIONS[doc.template_code] ?? '';
  const tone = DOC_STATUS_TONE[doc.status] ?? 'bg-zinc-100 text-zinc-700';
  const statusLabel = DOC_STATUS_LABELS[doc.status] ?? doc.status;
  const isPending =
    !doc.pdf_url && !doc.generation_error && doc.status === 'draft';
  const hasError = !!doc.generation_error;

  const deadline =
    doc.status === 'sent'
      ? computeDeadline(doc.template_code, doc.sent_at)
      : null;

  return (
    <article
      className={`flex flex-col gap-3 rounded-xl border p-4 ${
        isPreview
          ? 'border-primary/40 bg-primary/5'
          : 'border-on-surface/10 bg-white'
      }`}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          <FileText size={18} className="mt-0.5 text-on-surface-variant" />
          <div>
            <h3 className="text-sm font-semibold text-on-surface">{label}</h3>
            {description && (
              <p className="mt-0.5 text-xs text-on-surface-variant">
                {description}
              </p>
            )}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span
            className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium ${tone}`}
          >
            {statusLabel}
          </span>
          {deadline && <DeadlineChip deadline={deadline} />}
        </div>
      </div>

      {/* Timeline */}
      <DocTimeline status={doc.status} />

      {/* Timestamps */}
      {(doc.generated_at || doc.sent_at || doc.accepted_at || doc.rejected_at) && (
        <div className="flex flex-wrap gap-x-4 gap-y-0.5">
          {doc.generated_at && (
            <span className="text-[11px] text-on-surface-muted">
              Generato {formatDate(doc.generated_at)}
            </span>
          )}
          {doc.sent_at && (
            <span className="text-[11px] text-on-surface-muted">
              Inviato {formatDate(doc.sent_at)}
            </span>
          )}
          {doc.accepted_at && (
            <span className="text-[11px] text-emerald-600">
              Accettato {formatDate(doc.accepted_at)}
            </span>
          )}
          {doc.rejected_at && (
            <span className="text-[11px] text-rose-600">
              Respinto {formatDate(doc.rejected_at)}
            </span>
          )}
        </div>
      )}

      {isPending && (
        <div className="flex items-center gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
          <Loader2 size={14} className="animate-spin" />
          Generazione in corso…
        </div>
      )}

      {hasError && (
        <div className="flex items-start gap-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>{doc.generation_error}</span>
        </div>
      )}

      {doc.rejection_reason && (
        <div className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
          <span className="font-medium">Motivo: </span>
          {doc.rejection_reason}
        </div>
      )}

      {/* Action row */}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        {doc.pdf_url && (
          <a
            href={`${API_URL}/v1/practices/${practiceId}/documents/${doc.template_code}/download`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-white hover:bg-primary/90"
          >
            <Download size={13} /> Scarica
          </a>
        )}
        {doc.pdf_url && !isPreview && (
          <button
            type="button"
            onClick={onPreview}
            className="inline-flex items-center gap-1.5 rounded-lg border border-on-surface/10 px-3 py-1.5 text-xs font-medium text-on-surface-variant hover:bg-surface-container-lowest/40"
          >
            Anteprima
          </button>
        )}
        <button
          type="button"
          onClick={onRegenerate}
          disabled={isBusy}
          className="inline-flex items-center gap-1.5 rounded-lg border border-on-surface/10 px-3 py-1.5 text-xs font-medium text-on-surface-variant hover:bg-surface-container-lowest/40 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isBusy ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <RefreshCw size={13} />
          )}
          Rigenera
        </button>
        {(doc.status === 'reviewed' || doc.status === 'draft') && (
          <button
            type="button"
            onClick={onMarkSent}
            disabled={isBusy || !doc.pdf_url}
            className="inline-flex items-center gap-1.5 rounded-lg border border-on-surface/10 px-3 py-1.5 text-xs font-medium text-on-surface-variant hover:bg-surface-container-lowest/40 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Send size={13} /> Marca come inviato
          </button>
        )}
        {doc.status === 'sent' && (
          <span className="inline-flex items-center gap-1 text-[11px] text-on-surface-muted">
            <Check size={12} /> Inviato il {formatDate(doc.sent_at)}
          </span>
        )}
      </div>
    </article>
  );
}

// ---------------------------------------------------------------------------
// DocTimeline — horizontal step indicator
// ---------------------------------------------------------------------------

function DocTimeline({ status }: { status: string }) {
  // Choose the step track based on whether the document ended in rejection.
  const steps =
    status === 'rejected' || status === 'amended'
      ? REJECTED_STEPS
      : DOC_TIMELINE_STEPS;

  // Find the index of the current status (or the nearest past step).
  const currentIdx = steps.findIndex((s) => s.key === status);

  return (
    <div className="flex items-center gap-0 overflow-x-auto pt-1">
      {steps.map((step, idx) => {
        const isPast = idx < currentIdx;
        const isCurrent = idx === currentIdx;
        const isRejected = step.key === 'rejected' && isCurrent;

        return (
          <div key={step.key} className="flex min-w-0 flex-1 items-center">
            {/* Connector line (not for first step) */}
            {idx > 0 && (
              <div
                className={`h-px flex-1 ${
                  isPast || isCurrent ? 'bg-primary/60' : 'bg-on-surface/10'
                }`}
              />
            )}
            {/* Dot + label */}
            <div className="flex flex-col items-center gap-0.5 px-1">
              <div
                className={`flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold transition-colors ${
                  isRejected
                    ? 'bg-rose-500 text-white'
                    : isCurrent
                      ? 'bg-primary text-white'
                      : isPast
                        ? 'bg-primary/20 text-primary'
                        : 'bg-on-surface/10 text-on-surface-muted'
                }`}
              >
                {isRejected ? (
                  <X size={10} strokeWidth={3} />
                ) : isPast ? (
                  <Check size={10} strokeWidth={3} />
                ) : (
                  idx + 1
                )}
              </div>
              <span
                className={`whitespace-nowrap text-[9px] ${
                  isCurrent
                    ? 'font-semibold text-primary'
                    : isPast
                      ? 'text-primary/60'
                      : 'text-on-surface-muted'
                }`}
              >
                {step.label}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// DeadlineChip
// ---------------------------------------------------------------------------

function DeadlineChip({ deadline }: { deadline: DeadlineInfo }) {
  if (deadline.isOverdue) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2 py-0.5 text-[10px] font-semibold text-rose-700">
        <AlertTriangle size={9} />
        Scaduto da {Math.abs(deadline.daysRemaining)} gg
      </span>
    );
  }
  if (deadline.isImminent) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-700">
        <Clock size={9} />
        {deadline.daysRemaining} gg
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 text-[10px] text-blue-600">
      <Clock size={9} />
      {deadline.daysRemaining} gg
    </span>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function DataRow({
  label,
  value,
}: {
  label: string;
  value: string | null | undefined;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-muted">
        {label}
      </span>
      <span className="text-sm text-on-surface">
        {value && value.length ? value : '—'}
      </span>
    </div>
  );
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString('it-IT');
  } catch {
    return '';
  }
}

// ---------------------------------------------------------------------------
// EventTimelinePanel — audit log from practice_events
// ---------------------------------------------------------------------------

const EVENT_TYPE_LABELS: Record<string, string> = {
  practice_created: 'Pratica creata',
  practice_status_changed: 'Stato pratica aggiornato',
  practice_cancelled: 'Pratica annullata',
  document_generated: 'Documento generato',
  document_regenerated: 'Documento rigenerato',
  document_generation_failed: 'Generazione documento fallita',
  document_reviewed: 'Documento verificato',
  document_sent: 'Documento inviato',
  document_accepted: 'Documento accettato',
  document_rejected: 'Documento respinto',
  document_amended: 'Documento integrato',
  document_completed: 'Documento completato',
  deadline_created: 'Scadenza aperta',
  deadline_satisfied: 'Scadenza chiusa',
  deadline_breached: 'Scadenza superata',
  deadline_cancelled: 'Scadenza annullata',
  data_collected: 'Dati raccolti',
};

const EVENT_TONE: Record<string, string> = {
  document_rejected: 'border-rose-300 bg-rose-50 text-rose-700',
  document_generation_failed: 'border-rose-300 bg-rose-50 text-rose-700',
  deadline_breached: 'border-amber-300 bg-amber-50 text-amber-700',
  deadline_satisfied: 'border-emerald-300 bg-emerald-50 text-emerald-700',
  document_accepted: 'border-emerald-300 bg-emerald-50 text-emerald-700',
  document_completed: 'border-emerald-300 bg-emerald-50 text-emerald-700',
  practice_cancelled: 'border-rose-300 bg-rose-50 text-rose-700',
};

function EventTimelinePanel({ events }: { events: PracticeEvent[] }) {
  // Server returns ascending; the timeline reads top-down with the most
  // recent at the top — so reverse client-side.
  const ordered = useMemo(() => [...events].reverse(), [events]);

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold uppercase tracking-widest text-on-surface-variant">
        Timeline pratica
      </h2>
      <ol className="relative space-y-3 border-l border-on-surface/10 pl-5">
        {ordered.map((event) => {
          const label = EVENT_TYPE_LABELS[event.event_type] ?? event.event_type;
          const tone =
            EVENT_TONE[event.event_type] ??
            'border-on-surface/10 bg-white text-on-surface';
          const templateCode = event.payload?.['template_code'] as
            | string
            | undefined;
          const templateLabel =
            templateCode && TEMPLATE_LABELS[templateCode]
              ? TEMPLATE_LABELS[templateCode]
              : templateCode;
          return (
            <li
              key={event.id}
              className={`relative rounded-lg border px-4 py-3 text-sm ${tone}`}
            >
              <span className="absolute -left-[7px] top-4 size-3 rounded-full border border-on-surface/20 bg-white" />
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-medium">{label}</span>
                <time className="text-xs text-on-surface-variant">
                  {formatDateTime(event.occurred_at)}
                </time>
              </div>
              {templateLabel && (
                <div className="mt-1 text-xs text-on-surface-variant">
                  {templateLabel}
                </div>
              )}
              {typeof event.payload?.['rejection_reason'] === 'string' && (
                <div className="mt-1 text-xs">
                  Motivo:&nbsp;
                  <span className="font-medium">
                    {String(event.payload['rejection_reason'])}
                  </span>
                </div>
              )}
              {typeof event.payload?.['error'] === 'string' && (
                <div className="mt-1 text-xs">
                  Errore:&nbsp;
                  <span className="font-medium">
                    {String(event.payload['error'])}
                  </span>
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

// ---------------------------------------------------------------------------
// ServerDeadlinesPanel — explicit DEADLINE_RULES projection
// ---------------------------------------------------------------------------

const DEADLINE_KIND_TONE: Record<string, string> = {
  open: 'border-blue-300 bg-blue-50 text-blue-700',
  satisfied: 'border-emerald-300 bg-emerald-50 text-emerald-700',
  overdue: 'border-rose-400 bg-rose-50 text-rose-700',
  cancelled: 'border-on-surface/10 bg-white text-on-surface-variant',
};

const DEADLINE_STATUS_LABELS: Record<string, string> = {
  open: 'In attesa',
  satisfied: 'Risolta',
  overdue: 'Scaduta',
  cancelled: 'Annullata',
};

function ServerDeadlinesPanel({
  deadlines,
}: {
  deadlines: PracticeDeadline[];
}) {
  // Sort: overdue first, then open by due_at, then closed.
  const ordered = useMemo(() => {
    const order: Record<string, number> = {
      overdue: 0,
      open: 1,
      satisfied: 2,
      cancelled: 3,
    };
    return [...deadlines].sort((a, b) => {
      const oa = order[a.status] ?? 9;
      const ob = order[b.status] ?? 9;
      if (oa !== ob) return oa - ob;
      return new Date(a.due_at).getTime() - new Date(b.due_at).getTime();
    });
  }, [deadlines]);

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold uppercase tracking-widest text-on-surface-variant">
        Scadenze regolatorie
      </h2>
      <div className="grid gap-2 md:grid-cols-2">
        {ordered.map((d) => {
          const tone =
            DEADLINE_KIND_TONE[d.status] ?? 'border-on-surface/10 bg-white';
          const title =
            (d.metadata?.['title'] as string | undefined) ?? d.deadline_kind;
          const reference = d.metadata?.['reference'] as string | undefined;
          const statusLabel = DEADLINE_STATUS_LABELS[d.status] ?? d.status;
          return (
            <div
              key={d.id}
              className={`rounded-lg border px-4 py-3 text-sm ${tone}`}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-semibold">{title}</span>
                <span className="text-xs uppercase tracking-wide">
                  {statusLabel}
                </span>
              </div>
              <div className="mt-1 text-xs">
                Scadenza: {formatDateTime(d.due_at)}
                {d.satisfied_at && (
                  <> · risolta {formatDateTime(d.satisfied_at)}</>
                )}
              </div>
              {reference && (
                <div className="mt-1 text-xs italic text-on-surface-variant">
                  {reference}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function formatDateTime(iso: string | null): string {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('it-IT', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// MissingDataPanel — per-template gap analysis with inline edit forms
//
// Three sections:
//   1. "Dati installatore" — tenant fields → PATCH /v1/tenants/me
//   2. "Dati pratica"      — practice fields → PATCH /v1/practices/{id}
//   3. "Dati cliente"      — subject fields (read-only, link to lead)
// ---------------------------------------------------------------------------

/** Direct columns on the `practices` table (not stored in extras JSONB). */
const PRACTICE_DIRECT_COLUMNS = new Set([
  'impianto_pod',
  'impianto_pannelli_count',
  'impianto_potenza_kw',
  'impianto_data_inizio_lavori',
  'impianto_data_fine_lavori',
  'catastale_foglio',
  'catastale_particella',
  'catastale_subalterno',
  'impianto_distributore',
]);

function MissingDataPanel({
  practiceId,
  report,
  leadId,
  documents,
  onSaved,
}: {
  practiceId: string;
  report: MissingFieldsReport;
  leadId: string | null;
  /** Passed so we can auto-regenerate error'd docs after a tenant or practice save. */
  documents: DocumentRow[];
  onSaved: () => void;
}) {
  const tenantFields = report.by_source.tenant.filter(
    (f): f is MissingFieldItem & { api_field: string } => f.api_field !== null,
  );
  const practiceFields = report.by_source.practice.filter(
    (f): f is MissingFieldItem & { api_field: string } => f.api_field !== null,
  );
  const subjectFields = report.by_source.subject;

  // Form state — keyed by api_field name. Initialised empty on mount;
  // state is intentionally not reset when `report` prop refreshes so the
  // user doesn't lose partially-typed values across polls.
  const [tenantForm, setTenantForm] = useState<Record<string, string>>(() =>
    Object.fromEntries(tenantFields.map((f) => [f.api_field, ''])),
  );
  const [practiceForm, setPracticeForm] = useState<Record<string, string>>(() =>
    Object.fromEntries(practiceFields.map((f) => [f.api_field, ''])),
  );

  const [tenantBusy, setTenantBusy] = useState(false);
  const [practiceBusy, setPracticeBusy] = useState(false);
  const [tenantError, setTenantError] = useState<string | null>(null);
  const [practiceError, setPracticeError] = useState<string | null>(null);
  const [tenantSaved, setTenantSaved] = useState(false);
  const [practiceSaved, setPracticeSaved] = useState(false);

  /** Re-enqueue documents that previously failed (generation_error set). */
  async function regenerateErroredDocs() {
    const erroredCodes = documents
      .filter((d) => d.generation_error)
      .map((d) => d.template_code);
    await Promise.allSettled(
      erroredCodes.map((code) =>
        api.post(
          `/v1/practices/${practiceId}/documents/${code}/regenerate`,
          {},
        ),
      ),
    );
  }

  async function saveTenant() {
    setTenantBusy(true);
    setTenantError(null);
    try {
      const payload = Object.fromEntries(
        Object.entries(tenantForm).filter(([, v]) => v.trim() !== ''),
      );
      if (Object.keys(payload).length === 0) return;
      await api.patch('/v1/tenants/me', payload);
      // Re-render any documents that previously failed because tenant
      // fields were missing.
      await regenerateErroredDocs();
      setTenantSaved(true);
      onSaved();
    } catch (err) {
      setTenantError(
        err instanceof ApiError ? err.message : 'Salvataggio fallito.',
      );
    } finally {
      setTenantBusy(false);
    }
  }

  async function savePractice() {
    setPracticeBusy(true);
    setPracticeError(null);
    try {
      const direct: Record<string, unknown> = {};
      const extras: Record<string, unknown> = {};

      for (const [key, val] of Object.entries(practiceForm)) {
        if (val.trim() === '') continue;
        if (PRACTICE_DIRECT_COLUMNS.has(key)) {
          direct[key] = val;
        } else {
          extras[key] = val;
        }
      }

      if (
        Object.keys(direct).length === 0 &&
        Object.keys(extras).length === 0
      )
        return;

      const body: Record<string, unknown> = { ...direct };
      if (Object.keys(extras).length > 0) body.extras_patch = extras;

      await api.patch(`/v1/practices/${practiceId}?regenerate=true`, body);
      setPracticeSaved(true);
      onSaved();
    } catch (err) {
      setPracticeError(
        err instanceof ApiError ? err.message : 'Salvataggio fallito.',
      );
    } finally {
      setPracticeBusy(false);
    }
  }

  return (
    <section className="space-y-5 rounded-xl border border-amber-200 bg-amber-50 p-5">
      {/* Header */}
      <div className="flex items-start gap-2">
        <AlertTriangle
          size={16}
          className="mt-0.5 shrink-0 text-amber-600"
        />
        <div>
          <p className="text-sm font-semibold text-amber-800">
            Dati mancanti — completa per abilitare la generazione di tutti i
            documenti
          </p>
          <p className="mt-0.5 text-xs text-amber-700">
            Alcuni campi obbligatori non sono ancora presenti. Puoi
            compilarli qui senza uscire dalla pratica.
          </p>
        </div>
      </div>

      {/* Template-level readiness chips */}
      <div className="flex flex-wrap gap-2">
        {report.templates.map((t) => (
          <span
            key={t.template_code}
            className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${
              t.ready
                ? 'bg-emerald-100 text-emerald-700'
                : 'bg-rose-100 text-rose-700'
            }`}
          >
            {t.ready ? <Check size={10} /> : <X size={10} />}
            {TEMPLATE_LABELS[t.template_code] ?? t.template_code}
            {!t.ready &&
              ` — ${t.missing.length} campo${t.missing.length !== 1 ? 'i' : ''}`}
          </span>
        ))}
      </div>

      {/* ── 1. Dati installatore (tenant fields) ── */}
      {tenantFields.length > 0 && (
        <div className="space-y-3 rounded-lg bg-white/60 p-4">
          <h3 className="text-xs font-bold uppercase tracking-widest text-amber-700">
            Dati installatore
          </h3>
          <div className="grid gap-3 md:grid-cols-2">
            {tenantFields.map((field) => (
              <div key={field.api_field} className="flex flex-col gap-1">
                <label className="text-xs font-medium text-on-surface">
                  {field.label}
                </label>
                <input
                  type="text"
                  value={tenantForm[field.api_field] ?? ''}
                  onChange={(e) =>
                    setTenantForm((prev) => ({
                      ...prev,
                      [field.api_field]: e.target.value,
                    }))
                  }
                  placeholder={field.label}
                  className="rounded-lg border border-on-surface/20 bg-white px-3 py-1.5 text-sm text-on-surface outline-none focus:border-primary focus:ring-1 focus:ring-primary/20"
                />
              </div>
            ))}
          </div>
          {tenantError && (
            <p className="text-xs text-rose-700">{tenantError}</p>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={saveTenant}
              disabled={tenantBusy}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {tenantBusy ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <Check size={12} />
              )}
              Salva dati installatore
            </button>
            {tenantSaved && (
              <span className="flex items-center gap-1 text-xs text-emerald-700">
                <Check size={11} /> Salvato
              </span>
            )}
            <Link
              href="/settings/legal"
              className="text-xs text-primary hover:underline"
            >
              Apri Impostazioni legali →
            </Link>
          </div>
        </div>
      )}

      {/* ── 2. Dati pratica (practice direct + extras fields) ── */}
      {practiceFields.length > 0 && (
        <div className="space-y-3 rounded-lg bg-white/60 p-4">
          <h3 className="text-xs font-bold uppercase tracking-widest text-amber-700">
            Dati pratica
          </h3>
          <div className="grid gap-3 md:grid-cols-2">
            {practiceFields.map((field) => (
              <div key={field.api_field} className="flex flex-col gap-1">
                <label className="text-xs font-medium text-on-surface">
                  {field.label}
                </label>
                <input
                  type="text"
                  value={practiceForm[field.api_field] ?? ''}
                  onChange={(e) =>
                    setPracticeForm((prev) => ({
                      ...prev,
                      [field.api_field]: e.target.value,
                    }))
                  }
                  placeholder={field.label}
                  className="rounded-lg border border-on-surface/20 bg-white px-3 py-1.5 text-sm text-on-surface outline-none focus:border-primary focus:ring-1 focus:ring-primary/20"
                />
              </div>
            ))}
          </div>
          {practiceError && (
            <p className="text-xs text-rose-700">{practiceError}</p>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={savePractice}
              disabled={practiceBusy}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {practiceBusy ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <Check size={12} />
              )}
              Salva e rigenera documenti
            </button>
            {practiceSaved && (
              <span className="flex items-center gap-1 text-xs text-emerald-700">
                <Check size={11} /> Salvato — rigenerazione in corso
              </span>
            )}
          </div>
        </div>
      )}

      {/* ── 3. Dati cliente (subject — read-only, link to lead) ── */}
      {subjectFields.length > 0 && (
        <div className="space-y-3 rounded-lg bg-white/60 p-4">
          <h3 className="text-xs font-bold uppercase tracking-widest text-amber-700">
            Dati cliente (da completare sul lead)
          </h3>
          <ul className="space-y-1.5">
            {subjectFields.map((field) => (
              <li
                key={field.path}
                className="flex items-center gap-2 text-xs text-on-surface-variant"
              >
                <X size={10} className="shrink-0 text-rose-500" />
                {field.label}
              </li>
            ))}
          </ul>
          {leadId && (
            <Link
              href={`/leads/${leadId}`}
              className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
            >
              Modifica dati cliente →
            </Link>
          )}
        </div>
      )}
    </section>
  );
}
