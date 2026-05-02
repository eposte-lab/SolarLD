/**
 * /admin/demo-runs — cross-tenant demo pipeline run log.
 *
 * Super-admin only. Shows every customer-facing "Avvia test pipeline"
 * execution with live status, timeline, error messages, and deep-link
 * to the resulting lead.
 *
 * Data: GET /v1/admin/demo/runs (admin.py)
 * Auth: the API gate enforces super_admin role; the dashboard also
 *       blocks navigation to this page for non-super_admin users.
 */
'use client';

import { use, useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Info,
  Loader2,
  Mail,
  MailX,
  MapPin,
  RefreshCw,
  Send,
  Zap,
} from 'lucide-react';

import { BentoCard } from '@/components/ui/bento-card';
import { api, ApiError } from '@/lib/api-client';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * Outreach send statuses we surface here.  Mirrors
 * ``apps/api/src/models/enums.py::CampaignStatus`` plus a few in-flight
 * values that the TrackingAgent writes when Resend webhooks land.
 *
 * The two statuses that matter most for demo runs:
 *   - SENT     → Resend accepted the request (HTTP 2xx). Not delivery.
 *   - DELIVERED → recipient mailbox accepted the message (delivered webhook).
 *   - FAILED   → bounced, complained, or send-time error. ``email_status_detail``
 *                carries the bounce/complaint code.
 */
type EmailStatus =
  | 'SCHEDULED'
  | 'SENT'
  | 'DELIVERED'
  | 'OPENED'
  | 'CLICKED'
  | 'FAILED';

interface DemoRunRow {
  id: string;
  tenant_id: string;
  tenant_name: string | null;
  lead_id: string | null;
  status: 'scoring' | 'creative' | 'outreach' | 'done' | 'failed';
  failed_step: string | null;
  error_message: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
  // Email truth: what really happened after Resend accepted the send.
  email_status: EmailStatus | null;
  email_status_detail: string | null;
  email_message_id: string | null;
  email_sent_at: string | null;
  email_recipient: string | null;
  // Roof identification provenance (Sprint 2 cascade).
  roof_source:
    | 'atoka'
    | 'website_scrape'
    | 'google_places'
    | 'mapbox_hq'
    | 'osm_snap'
    | 'unresolved'
    | null;
  roof_confidence: 'high' | 'medium' | 'low' | 'none' | null;
}

interface DemoRunsResponse {
  runs: DemoRunRow[];
  total: number;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 50;

const STATUS_STEPS = ['scoring', 'creative', 'outreach', 'done'] as const;

const STATUS_CONFIG: Record<
  DemoRunRow['status'],
  { label: string; tone: string; icon: React.FC<{ className?: string }> }
> = {
  scoring: {
    label: 'Scoring',
    tone: 'bg-blue-500/15 text-blue-300',
    icon: ({ className }) => <Zap className={className} />,
  },
  creative: {
    label: 'Creative',
    tone: 'bg-violet-500/15 text-violet-300',
    icon: ({ className }) => <Zap className={className} />,
  },
  outreach: {
    label: 'Outreach',
    tone: 'bg-amber-500/15 text-amber-300',
    icon: ({ className }) => <Clock className={className} />,
  },
  done: {
    label: 'Completato',
    tone: 'bg-emerald-500/15 text-emerald-300',
    icon: ({ className }) => <CheckCircle2 className={className} />,
  },
  failed: {
    label: 'Fallito',
    tone: 'bg-rose-500/15 text-rose-300',
    icon: ({ className }) => <AlertTriangle className={className} />,
  },
};

const STATUS_FILTER_OPTIONS = [
  { value: '', label: 'Tutti' },
  { value: 'done', label: 'Completati' },
  { value: 'failed', label: 'Falliti' },
  { value: 'scoring', label: 'Scoring' },
  { value: 'creative', label: 'Creative' },
  { value: 'outreach', label: 'Outreach' },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDateTime(iso: string): string {
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

function elapsedSeconds(start: string, end: string): number {
  return Math.round((new Date(end).getTime() - new Date(start).getTime()) / 1000);
}

// ---------------------------------------------------------------------------
// Timeline bar — shows progress through scoring → creative → outreach → done
// ---------------------------------------------------------------------------

function TimelineBar({ run }: { run: DemoRunRow }) {
  const currentIdx = STATUS_STEPS.indexOf(run.status as (typeof STATUS_STEPS)[number]);
  const isFailed = run.status === 'failed';
  const failedAt = run.failed_step;

  return (
    <div className="flex items-center gap-0">
      {STATUS_STEPS.map((step, idx) => {
        const isCompleted = run.status === 'done' || (currentIdx > idx && !isFailed);
        const isCurrent = run.status === step;
        const isFailedStep = isFailed && failedAt === step;
        const isPending = !isCompleted && !isCurrent && !isFailedStep;

        return (
          <div key={step} className="flex items-center">
            {/* Step dot */}
            <div
              className={cn(
                'flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold transition-colors',
                isCompleted && 'bg-emerald-500/25 text-emerald-300',
                isCurrent && 'animate-pulse bg-blue-500/25 text-blue-300',
                isFailedStep && 'bg-rose-500/25 text-rose-300',
                isPending && 'bg-on-surface/8 text-on-surface-variant',
              )}
              title={step}
            >
              {isCompleted ? (
                <CheckCircle2 className="h-3 w-3" />
              ) : isFailedStep ? (
                <AlertTriangle className="h-3 w-3" />
              ) : isCurrent ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <span>{idx + 1}</span>
              )}
            </div>
            {/* Connector */}
            {idx < STATUS_STEPS.length - 1 && (
              <div
                className={cn(
                  'h-px w-6',
                  isCompleted ? 'bg-emerald-500/30' : 'bg-on-surface/8',
                )}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Expandable error / notes row
// ---------------------------------------------------------------------------

function ErrorNotesBadge({ run }: { run: DemoRunRow }) {
  const [open, setOpen] = useState(false);
  const hasError = Boolean(run.error_message);
  const hasNotes = Boolean(run.notes);

  if (!hasError && !hasNotes) return null;

  return (
    <div className="mt-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'inline-flex items-center gap-1 rounded text-[11px] font-medium transition-colors',
          hasError
            ? 'text-rose-400 hover:text-rose-300'
            : 'text-amber-400 hover:text-amber-300',
        )}
      >
        {hasError ? (
          <AlertTriangle className="h-3 w-3" />
        ) : (
          <Info className="h-3 w-3" />
        )}
        {hasError ? 'Errore' : 'Nota'}
        <span className="ml-0.5">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div
          className={cn(
            'mt-1.5 rounded-lg px-3 py-2 text-xs font-mono leading-relaxed',
            hasError
              ? 'bg-rose-500/10 text-rose-300'
              : 'bg-amber-500/10 text-amber-300',
          )}
        >
          {run.error_message && (
            <p className="mb-1">
              <span className="font-semibold">Errore</span>
              {run.failed_step && (
                <span className="ml-1 text-rose-400/70">[step: {run.failed_step}]</span>
              )}
              : {run.error_message}
            </p>
          )}
          {run.notes && (
            <p>
              <span className="font-semibold">Nota</span>: {run.notes}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Email delivery chip — renders the *real* state of the demo email,
// distinguishing "Resend accepted it" (sent) from "actually arrived"
// (delivered) and from "bounced/complained" (failed). Tooltip surfaces
// the failure reason and the recipient address so an operator can spot
// silent ``DEMO_EMAIL_RECIPIENT_OVERRIDE`` redirects at a glance.
// ---------------------------------------------------------------------------

const EMAIL_STATUS_CONFIG: Record<
  EmailStatus,
  { label: string; tone: string; icon: React.FC<{ className?: string }> }
> = {
  SCHEDULED: {
    label: 'In coda',
    tone: 'bg-on-surface/8 text-on-surface-variant',
    icon: ({ className }) => <Clock className={className} />,
  },
  SENT: {
    label: 'Accettata',
    tone: 'bg-amber-500/15 text-amber-300',
    icon: ({ className }) => <Send className={className} />,
  },
  DELIVERED: {
    label: 'Consegnata',
    tone: 'bg-emerald-500/15 text-emerald-300',
    icon: ({ className }) => <Mail className={className} />,
  },
  OPENED: {
    label: 'Aperta',
    tone: 'bg-emerald-500/20 text-emerald-200',
    icon: ({ className }) => <Mail className={className} />,
  },
  CLICKED: {
    label: 'Cliccata',
    tone: 'bg-emerald-500/25 text-emerald-200',
    icon: ({ className }) => <Mail className={className} />,
  },
  FAILED: {
    label: 'Errore',
    tone: 'bg-rose-500/15 text-rose-300',
    icon: ({ className }) => <MailX className={className} />,
  },
};

function EmailStatusChip({ run }: { run: DemoRunRow }) {
  // Outreach step hasn't started yet → nothing to show.
  if (run.status === 'scoring' || run.status === 'creative') {
    return <span className="text-xs text-on-surface-muted">—</span>;
  }
  // Outreach step ran but the send wasn't recorded — could be the
  // OutreachAgent failed before the insert, or Resend rejected the
  // request. The ``error_message`` on the run row carries the detail.
  if (!run.email_status) {
    return run.status === 'failed' && run.failed_step === 'outreach' ? (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-500/15 px-2 py-0.5 text-[11px] font-semibold text-rose-300">
        <MailX className="h-3 w-3" />
        Non inviata
      </span>
    ) : (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-on-surface/8 px-2 py-0.5 text-[11px] font-medium text-on-surface-variant">
        <Loader2 className="h-3 w-3 animate-spin" />
        In invio…
      </span>
    );
  }
  const cfg = EMAIL_STATUS_CONFIG[run.email_status] ?? EMAIL_STATUS_CONFIG.SENT;
  const Icon = cfg.icon;
  // Tooltip text — recipient + bounce code so operators don't need to
  // jump to Resend dashboard for the obvious cases (bounced mailbox,
  // recipient mismatch when the override env var is set).
  const tooltipParts: string[] = [];
  if (run.email_recipient) tooltipParts.push(`A: ${run.email_recipient}`);
  if (run.email_status_detail) tooltipParts.push(`Motivo: ${run.email_status_detail}`);
  if (run.email_message_id) tooltipParts.push(`Resend ID: ${run.email_message_id}`);
  const tooltip = tooltipParts.join('\n');

  return (
    <span
      title={tooltip || undefined}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold',
        cfg.tone,
      )}
    >
      <Icon className="h-3 w-3" />
      {cfg.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Roof identification badge — shows which tier of the operating-site
// cascade actually produced the rooftop coords. Red ``Centroide HQ`` /
// ``Non risolto`` flag the runs where the rendered roof is almost
// certainly the wrong building (hand-review before showing the lead
// in a demo call).
// ---------------------------------------------------------------------------

const ROOF_SOURCE_CONFIG: Record<
  NonNullable<DemoRunRow['roof_source']>,
  { label: string; tone: string }
> = {
  atoka: {
    label: 'Atoka',
    tone: 'bg-emerald-500/15 text-emerald-300',
  },
  website_scrape: {
    label: 'Sito web',
    tone: 'bg-emerald-500/15 text-emerald-300',
  },
  osm_snap: {
    label: 'Snap OSM',
    tone: 'bg-amber-500/15 text-amber-300',
  },
  google_places: {
    label: 'Google Places',
    tone: 'bg-amber-500/15 text-amber-300',
  },
  mapbox_hq: {
    label: 'Centroide HQ',
    tone: 'bg-rose-500/15 text-rose-300',
  },
  unresolved: {
    label: 'Non risolto',
    tone: 'bg-on-surface/10 text-on-surface-muted',
  },
};

function RoofBadge({ run }: { run: DemoRunRow }) {
  if (!run.roof_source) {
    return <span className="text-xs text-on-surface-muted">—</span>;
  }
  const cfg = ROOF_SOURCE_CONFIG[run.roof_source] ?? null;
  if (!cfg) return <span className="text-xs text-on-surface-muted">—</span>;
  const tooltip = run.roof_confidence
    ? `Confidence: ${run.roof_confidence}`
    : undefined;
  return (
    <span
      title={tooltip}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold',
        cfg.tone,
      )}
    >
      <MapPin className="h-3 w-3" />
      {cfg.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Filter chip
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AdminDemoRunsPage() {
  const [runs, setRuns] = useState<DemoRunRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [statusFilter, setStatusFilter] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String(page * PAGE_SIZE),
    });
    if (statusFilter) params.set('status', statusFilter);
    try {
      const data = await api.get<DemoRunsResponse>(
        `/v1/admin/demo/runs?${params.toString()}`,
      );
      setRuns(data.runs);
      setTotal(data.total);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setError('Accesso negato. Questa pagina richiede il ruolo super_admin.');
      } else {
        setError(
          err instanceof ApiError ? err.message : 'Errore caricamento run demo.',
        );
      }
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  // Auto-refresh every 10s when active runs are in-flight
  useEffect(() => {
    if (!autoRefresh) return;
    const t = setInterval(() => void load(), 10_000);
    return () => clearInterval(t);
  }, [autoRefresh, load]);

  // Stats — pipeline state plus delivery-truth counts that surface
  // silent bounces and mis-routed sends. ``deliveredOk`` and
  // ``deliveryFailed`` derive from ``email_status``, which only the
  // Resend webhook can set — so they double as a heartbeat for the
  // webhook integration itself.
  const stats = useMemo(() => {
    const done = runs.filter((r) => r.status === 'done').length;
    const failed = runs.filter((r) => r.status === 'failed').length;
    const inFlight = runs.filter(
      (r) => r.status === 'scoring' || r.status === 'creative' || r.status === 'outreach',
    ).length;
    const deliveredOk = runs.filter(
      (r) =>
        r.email_status === 'DELIVERED' ||
        r.email_status === 'OPENED' ||
        r.email_status === 'CLICKED',
    ).length;
    const deliveryPending = runs.filter((r) => r.email_status === 'SENT').length;
    const deliveryFailed = runs.filter((r) => r.email_status === 'FAILED').length;
    const lowConfidenceRoof = runs.filter(
      (r) => r.roof_source === 'mapbox_hq' || r.roof_source === 'unresolved',
    ).length;
    return {
      done,
      failed,
      inFlight,
      deliveredOk,
      deliveryPending,
      deliveryFailed,
      lowConfidenceRoof,
    };
  }, [runs]);

  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="space-y-6">
      {/* Header */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Super Admin · Demo pipeline
          </p>
          <h1 className="font-headline text-4xl font-bold tracking-tighter text-on-surface">
            Test runs
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-on-surface-variant">
            Ogni esecuzione di &ldquo;Avvia test pipeline&rdquo; dai tenant demo. Log
            cross-tenant in tempo reale.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setAutoRefresh((v) => !v)}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-semibold transition-colors',
              autoRefresh
                ? 'bg-emerald-500/20 text-emerald-300'
                : 'bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface',
            )}
          >
            <Zap className="h-3.5 w-3.5" />
            {autoRefresh ? 'Live (10s)' : 'Auto-refresh'}
          </button>
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
      {!loading && !error && (
        <div className="flex flex-wrap gap-2">
          <div className="inline-flex items-center gap-1.5 rounded-full bg-surface-container-high px-3 py-1.5 text-sm font-medium text-on-surface-variant">
            Totale: <span className="ml-1 font-bold text-on-surface">{total.toLocaleString('it-IT')}</span>
          </div>
          {stats.inFlight > 0 && (
            <div className="inline-flex items-center gap-1.5 rounded-full bg-blue-500/15 px-3 py-1.5 text-sm font-semibold text-blue-300">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {stats.inFlight} in corso
            </div>
          )}
          {stats.done > 0 && (
            <div className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-3 py-1.5 text-sm font-medium text-emerald-300">
              <CheckCircle2 className="h-3.5 w-3.5" />
              {stats.done} completati
            </div>
          )}
          {stats.failed > 0 && (
            <div className="inline-flex items-center gap-1.5 rounded-full bg-rose-500/15 px-3 py-1.5 text-sm font-semibold text-rose-300">
              <AlertTriangle className="h-3.5 w-3.5" />
              {stats.failed} falliti
            </div>
          )}
          {/* Delivery truth chips — populated only when the Resend webhook
              has landed for at least one run. If they stay at zero across a
              page of completed runs, the webhook integration is broken (or
              no Resend events have arrived yet). */}
          {stats.deliveredOk > 0 && (
            <div className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-3 py-1.5 text-sm font-medium text-emerald-300">
              <Mail className="h-3.5 w-3.5" />
              {stats.deliveredOk} consegnate
            </div>
          )}
          {stats.deliveryPending > 0 && (
            <div className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/15 px-3 py-1.5 text-sm font-medium text-amber-300">
              <Send className="h-3.5 w-3.5" />
              {stats.deliveryPending} in attesa
            </div>
          )}
          {stats.deliveryFailed > 0 && (
            <div className="inline-flex items-center gap-1.5 rounded-full bg-rose-500/15 px-3 py-1.5 text-sm font-semibold text-rose-300">
              <MailX className="h-3.5 w-3.5" />
              {stats.deliveryFailed} bouncate
            </div>
          )}
          {stats.lowConfidenceRoof > 0 && (
            <div
              className="inline-flex items-center gap-1.5 rounded-full bg-orange-500/15 px-3 py-1.5 text-sm font-semibold text-orange-300"
              title="Run con tetto identificato solo via centroide HQ o non risolto — verifica manuale prima di mostrare in demo"
            >
              <MapPin className="h-3.5 w-3.5" />
              {stats.lowConfidenceRoof} tetto da verificare
            </div>
          )}
        </div>
      )}

      {/* Filters */}
      <BentoCard padding="tight" span="full">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2">
          <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Stato
          </span>
          <div className="flex flex-wrap gap-1.5">
            {STATUS_FILTER_OPTIONS.map((opt) => (
              <FilterChip
                key={opt.value || 'all'}
                active={statusFilter === opt.value}
                onClick={() => {
                  setStatusFilter(opt.value);
                  setPage(0);
                }}
              >
                {opt.label}
              </FilterChip>
            ))}
          </div>
        </div>
      </BentoCard>

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
      {!loading && !error && runs.length === 0 && (
        <BentoCard padding="loose" span="full">
          <div className="py-12 text-center text-sm text-on-surface-variant">
            <Zap className="mx-auto mb-3 h-10 w-10 text-on-surface-muted" strokeWidth={1.4} />
            Nessun test run trovato
            {statusFilter && ` per stato "${statusFilter}"`}.
          </div>
        </BentoCard>
      )}

      {/* Table */}
      {!loading && runs.length > 0 && (
        <BentoCard padding="tight" span="full" className="overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-on-surface/8 bg-surface-container-lowest/50">
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                  Tenant
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                  Stato
                </th>
                <th className="hidden px-4 py-3 text-left text-xs font-semibold uppercase tracking-widest text-on-surface-variant md:table-cell">
                  Timeline
                </th>
                <th className="hidden px-4 py-3 text-left text-xs font-semibold uppercase tracking-widest text-on-surface-variant lg:table-cell">
                  Email
                </th>
                <th className="hidden px-4 py-3 text-left text-xs font-semibold uppercase tracking-widest text-on-surface-variant lg:table-cell">
                  Tetto
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                  Avviato
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                  Durata
                </th>
                <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                  Lead
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-on-surface/6">
              {runs.map((run) => {
                const cfg = STATUS_CONFIG[run.status];
                const StatusIcon = cfg.icon;
                const elapsed = elapsedSeconds(run.created_at, run.updated_at);

                return (
                  <tr
                    key={run.id}
                    className={cn(
                      'transition-colors hover:bg-surface-container-low',
                      run.status === 'failed' && 'bg-rose-500/5',
                      (run.status === 'scoring' ||
                        run.status === 'creative' ||
                        run.status === 'outreach') &&
                        'bg-blue-500/5',
                    )}
                  >
                    {/* Tenant */}
                    <td className="px-4 py-4">
                      <div className="font-medium text-on-surface">
                        {run.tenant_name ?? '—'}
                      </div>
                      <div className="mt-0.5 font-mono text-[10px] text-on-surface-variant">
                        {run.tenant_id.slice(0, 8)}…
                      </div>
                      <ErrorNotesBadge run={run} />
                    </td>

                    {/* Status chip */}
                    <td className="px-4 py-4">
                      <span
                        className={cn(
                          'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold',
                          cfg.tone,
                        )}
                      >
                        <StatusIcon className="h-3 w-3" />
                        {cfg.label}
                      </span>
                    </td>

                    {/* Timeline bar */}
                    <td className="hidden px-4 py-4 md:table-cell">
                      <TimelineBar run={run} />
                    </td>

                    {/* Email delivery state — the truth column, distinct
                        from the pipeline ``status`` so a green outreach
                        dot can still surface a red bounce here. */}
                    <td className="hidden px-4 py-4 lg:table-cell">
                      <EmailStatusChip run={run} />
                    </td>

                    {/* Roof identification provenance */}
                    <td className="hidden px-4 py-4 lg:table-cell">
                      <RoofBadge run={run} />
                    </td>

                    {/* Started at */}
                    <td className="px-4 py-4 text-xs text-on-surface-variant">
                      {formatDateTime(run.created_at)}
                    </td>

                    {/* Duration */}
                    <td className="px-4 py-4 text-xs text-on-surface-variant">
                      {run.status === 'done' || run.status === 'failed' ? (
                        elapsed >= 60 ? (
                          `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`
                        ) : (
                          `${elapsed}s`
                        )
                      ) : (
                        <span className="inline-flex items-center gap-1 text-blue-400">
                          <Loader2 className="h-3 w-3 animate-spin" />
                          in corso
                        </span>
                      )}
                    </td>

                    {/* Lead link */}
                    <td className="px-4 py-4 text-right">
                      {run.lead_id ? (
                        <Link
                          href={`/leads/${run.lead_id}`}
                          className="rounded-md bg-primary/10 px-3 py-1.5 text-xs font-semibold text-primary hover:bg-primary/20"
                        >
                          Apri lead
                        </Link>
                      ) : (
                        <span className="text-xs text-on-surface-muted">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between border-t border-on-surface/8 px-4 py-3">
              <span className="text-xs text-on-surface-variant">
                {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} di {total.toLocaleString('it-IT')}
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={page === 0}
                  className="rounded-md p-1.5 text-on-surface-variant hover:bg-surface-container-high disabled:opacity-40"
                >
                  <ChevronLeft className="h-4 w-4" />
                </button>
                <span className="min-w-[60px] text-center text-xs text-on-surface-variant">
                  {page + 1} / {totalPages}
                </span>
                <button
                  onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                  disabled={page >= totalPages - 1}
                  className="rounded-md p-1.5 text-on-surface-variant hover:bg-surface-container-high disabled:opacity-40"
                >
                  <ChevronRight className="h-4 w-4" />
                </button>
              </div>
            </div>
          )}
        </BentoCard>
      )}
    </div>
  );
}
