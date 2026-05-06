'use client';

/**
 * FollowupBulkPanel — operator-driven follow-up con selettore lead.
 *
 * Tre modalità di selezione, mutuamente esclusive:
 *   1. **Manuale**     — incolla UUID dei lead nella textarea, uno per riga.
 *   2. **Filtri**      — applica criteri (score min, engagement min,
 *                        giorni-da-outreach, pipeline_status) e carica
 *                        gli ID che li soddisfano via GET /v1/leads.
 *   3. **Tutti**       — carica tutti i lead "attivi" (outreach inviato,
 *                        non terminali) — niente filtri.
 *
 * Quando il toggle `followup_auto_enabled` è ON, mostriamo un warning
 * in testa che spiega il rischio di duplicato: il cron 08:15 UTC potrebbe
 * inviare un secondo follow-up al lead che hai appena gestito qui.
 *
 * Generazione bozze:
 *   - "Genera bozze AI" (review-first, default)
 *   - "Genera e invia tutto" (skip review, conferma extra)
 * Entrambi chiamano POST /v1/followup/bulk-draft con send_immediately.
 */

import { useState } from 'react';
import Link from 'next/link';
import { AlertTriangle } from 'lucide-react';

import { api, ApiError } from '@/lib/api-client';
import { cn } from '@/lib/utils';

interface DraftResult {
  lead_id: string;
  ok: boolean;
  subject: string | null;
  body: string | null;
  error: string | null;
}

type DraftState = DraftResult & {
  sendStatus: 'idle' | 'sending' | 'sent' | 'error';
  sendError?: string;
};

type SelectionMode = 'manual' | 'filters' | 'all';

interface Filters {
  score_min: number;
  engagement_min: number;
  days_since_outreach_min: number;
  pipeline_status: string[]; // multi-select
}

const DEFAULT_FILTERS: Filters = {
  score_min: 50,
  engagement_min: 0,
  days_since_outreach_min: 4,
  pipeline_status: ['sent', 'clicked', 'engaged'],
};

const PIPELINE_STATUS_OPTIONS = [
  { value: 'sent', label: 'Inviata' },
  { value: 'clicked', label: 'Click' },
  { value: 'engaged', label: 'Engaged' },
  { value: 'whatsapp_sent', label: 'WhatsApp' },
  { value: 'appointment_set', label: 'Appuntamento' },
];

interface Props {
  tenantId: string;
  /** When true the auto-cron will also send follow-ups; warn the
   *  operator that running this panel might create duplicates. */
  autoCronEnabled?: boolean;
}

export function FollowupBulkPanel({
  tenantId: _tenantId,
  autoCronEnabled = false,
}: Props) {
  const [mode, setMode] = useState<SelectionMode>('manual');
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [leadIdsText, setLeadIdsText] = useState('');
  const [generatingStatus, setGeneratingStatus] = useState<
    'idle' | 'loading' | 'done' | 'error'
  >('idle');
  const [drafts, setDrafts] = useState<DraftState[]>([]);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [loadingLeads, setLoadingLeads] = useState(false);

  function parseLeadIds(): string[] {
    return leadIdsText
      .split(/[\n,;]+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
  }

  async function loadLeadsByMode() {
    setLoadingLeads(true);
    setGlobalError(null);
    try {
      // Build query string. When mode='all' we omit the filter params
      // entirely; the server falls back to "all leads with outreach
      // sent and not in a terminal pipeline state".
      const qs = new URLSearchParams();
      qs.set('per_page', '200');
      // Always exclude terminal statuses (the API has no NOT-IN
      // currently — we whitelist instead via pipeline_status_in).
      // For 'all' we whitelist every non-terminal status so the
      // single source of truth lives on the server.
      const ALL_NON_TERMINAL = [
        'sent',
        'clicked',
        'engaged',
        'whatsapp_sent',
        'appointment_set',
      ];
      if (mode === 'filters') {
        qs.set('score_min', String(filters.score_min));
        qs.set('engagement_min', String(filters.engagement_min));
        qs.set(
          'days_since_outreach_min',
          String(filters.days_since_outreach_min),
        );
        if (filters.pipeline_status.length > 0) {
          qs.set('pipeline_status_in', filters.pipeline_status.join(','));
        }
      } else if (mode === 'all') {
        qs.set('pipeline_status_in', ALL_NON_TERMINAL.join(','));
      }

      const res = await api.get<{ data: Array<{ id: string }> }>(
        `/v1/leads?${qs.toString()}`,
      );
      const ids = (res.data ?? []).map((r) => r.id);
      setLeadIdsText(ids.join('\n'));
      if (ids.length === 0) {
        setGlobalError(
          'Nessun lead trovato con i criteri selezionati. Allenta i filtri o passa a "Manuale".',
        );
      }
    } catch (err) {
      setGlobalError(
        err instanceof ApiError
          ? err.message
          : 'Errore caricamento lead. Riprova.',
      );
    } finally {
      setLoadingLeads(false);
    }
  }

  async function generateDrafts(sendImmediately = false) {
    const ids = parseLeadIds();
    if (ids.length === 0) return;
    if (ids.length > 50) {
      setGlobalError(
        'Massimo 50 lead per richiesta. Riduci la selezione (la cron processa tutti gli altri da sola).',
      );
      return;
    }
    if (sendImmediately) {
      const ok = window.confirm(
        `Stai per generare e INVIARE ${ids.length} follow-up senza revisione. Procedere?` +
          (autoCronEnabled
            ? '\n\nIl follow-up automatico è attivo: alcuni di questi lead potrebbero ricevere un secondo invio dal cron domani mattina.'
            : ''),
      );
      if (!ok) return;
    }
    setGeneratingStatus('loading');
    setGlobalError(null);
    setDrafts([]);
    try {
      const results = await api.post<DraftResult[]>('/v1/followup/bulk-draft', {
        lead_ids: ids,
        send_immediately: sendImmediately,
      });
      setDrafts(
        results.map((r) => ({
          ...r,
          sendStatus: sendImmediately && r.ok ? 'sent' : 'idle',
        })),
      );
      setGeneratingStatus('done');
    } catch (err) {
      setGlobalError(
        err instanceof ApiError
          ? err.message
          : 'Errore inatteso durante la generazione. Riprova tra qualche minuto.',
      );
      setGeneratingStatus('error');
    }
  }

  async function sendDraft(idx: number) {
    const draft = drafts[idx];
    if (!draft || !draft.ok || !draft.subject || !draft.body) return;
    setDrafts((prev) =>
      prev.map((d, i) => (i === idx ? { ...d, sendStatus: 'sending' } : d)),
    );
    try {
      await api.post(`/v1/leads/${draft.lead_id}/send-draft`, {
        subject: draft.subject,
        body: draft.body,
      });
      setDrafts((prev) =>
        prev.map((d, i) => (i === idx ? { ...d, sendStatus: 'sent' } : d)),
      );
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Errore durante l'invio.";
      setDrafts((prev) =>
        prev.map((d, i) =>
          i === idx ? { ...d, sendStatus: 'error', sendError: msg } : d,
        ),
      );
    }
  }

  function togglePipelineStatus(value: string) {
    setFilters((f) => ({
      ...f,
      pipeline_status: f.pipeline_status.includes(value)
        ? f.pipeline_status.filter((s) => s !== value)
        : [...f.pipeline_status, value],
    }));
  }

  const leadIds = parseLeadIds();

  return (
    <div className="space-y-4">
      {/* Auto-cron warning — visible only when the cron is enabled.
          Goes above EVERYTHING because it changes how the operator
          should reason about the panel. */}
      {autoCronEnabled && (
        <div className="flex items-start gap-2 rounded-lg bg-warning-container/30 px-3 py-2.5 text-sm text-on-warning-container">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
          <div>
            <p className="font-semibold">
              Attenzione: il follow-up automatico è attivo.
            </p>
            <p className="mt-0.5">
              Se invii adesso un follow-up manuale a un lead, il cron domani
              mattina potrebbe inviarne un secondo (duplicato). Disattiva il
              toggle in cima alla pagina se vuoi gestire l&apos;invio solo da
              qui.
            </p>
          </div>
        </div>
      )}

      {/* Mode selector */}
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-on-surface">
          Modalità selezione lead
        </legend>
        <div className="flex flex-wrap gap-2">
          {(
            [
              { value: 'manual', label: 'Manuale', desc: 'Incolla gli UUID' },
              { value: 'filters', label: 'Filtri', desc: 'Score, engagement, età outreach' },
              { value: 'all', label: 'Tutti', desc: 'Tutti i lead attivi' },
            ] as Array<{ value: SelectionMode; label: string; desc: string }>
          ).map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setMode(opt.value)}
              className={cn(
                'flex flex-col items-start gap-0.5 rounded-lg border px-3 py-2 text-left text-xs transition-colors',
                mode === opt.value
                  ? 'border-primary bg-primary/10 text-on-surface'
                  : 'border-outline-variant/40 bg-surface-container-lowest text-on-surface-variant hover:border-primary/40 hover:text-on-surface',
              )}
            >
              <span className="text-sm font-semibold">{opt.label}</span>
              <span className="text-[11px] opacity-80">{opt.desc}</span>
            </button>
          ))}
        </div>
      </fieldset>

      {/* Filters block — only when mode='filters' */}
      {mode === 'filters' && (
        <div className="space-y-3 rounded-xl border border-outline-variant/30 bg-surface-container-lowest p-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <NumberSlider
              label="Score minimo"
              value={filters.score_min}
              min={0}
              max={100}
              onChange={(n) => setFilters((f) => ({ ...f, score_min: n }))}
            />
            <NumberSlider
              label="Engagement minimo"
              value={filters.engagement_min}
              min={0}
              max={100}
              onChange={(n) =>
                setFilters((f) => ({ ...f, engagement_min: n }))
              }
            />
            <NumberSlider
              label="Giorni da outreach (min)"
              value={filters.days_since_outreach_min}
              min={0}
              max={30}
              onChange={(n) =>
                setFilters((f) => ({ ...f, days_since_outreach_min: n }))
              }
            />
          </div>
          <div>
            <label className="text-xs font-medium text-on-surface-variant">
              Stato pipeline
            </label>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {PIPELINE_STATUS_OPTIONS.map((opt) => {
                const active = filters.pipeline_status.includes(opt.value);
                return (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => togglePipelineStatus(opt.value)}
                    className={cn(
                      'rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-colors',
                      active
                        ? 'border-primary bg-primary/15 text-primary'
                        : 'border-outline-variant/40 bg-surface-container text-on-surface-variant hover:border-primary/30',
                    )}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Load button — visible for filters / all */}
      {mode !== 'manual' && (
        <button
          type="button"
          onClick={loadLeadsByMode}
          disabled={loadingLeads}
          className={cn(
            'inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold',
            'bg-surface-container text-on-surface shadow-ambient-sm transition-colors',
            'hover:bg-surface-container-high',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
        >
          {loadingLeads ? (
            <>
              <SpinnerIcon /> Caricamento…
            </>
          ) : mode === 'all' ? (
            'Carica tutti i lead attivi'
          ) : (
            'Applica filtri e carica lead'
          )}
        </button>
      )}

      {/* Lead-IDs textarea — always visible. In filter/all mode it
          shows what was loaded; the operator can still edit by hand. */}
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <label className="text-sm font-medium text-on-surface">
            ID lead selezionati (max 50 per invio)
          </label>
          {leadIds.length > 0 && (
            <span className="text-xs text-on-surface-variant">
              {leadIds.length} lead
            </span>
          )}
        </div>
        <textarea
          value={leadIdsText}
          onChange={(e) => setLeadIdsText(e.target.value)}
          rows={4}
          placeholder={
            mode === 'manual'
              ? 'Incolla qui gli UUID dei lead, uno per riga'
              : 'Clicca "Carica" sopra per popolare l\'elenco'
          }
          className={cn(
            'w-full resize-y rounded-lg border border-outline-variant/40 bg-surface-container-lowest',
            'px-3 py-2 font-mono text-xs text-on-surface placeholder-on-surface-variant/50',
            'focus:border-primary/60 focus:outline-none',
          )}
        />
      </div>

      {globalError && (
        <div className="flex items-start gap-2 rounded-lg bg-error-container/40 px-3 py-2 text-sm text-on-error-container">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
          <p>{globalError}</p>
        </div>
      )}

      {/* Generation buttons */}
      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={() => generateDrafts(false)}
          disabled={leadIds.length === 0 || generatingStatus === 'loading'}
          className={cn(
            'inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold',
            'bg-surface-container text-on-surface shadow-ambient-sm transition-colors',
            'hover:bg-surface-container-high',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
        >
          {generatingStatus === 'loading' ? (
            <>
              <SpinnerIcon />
              Generazione bozze…
            </>
          ) : (
            <>
              <SparkleIcon />
              Genera bozze AI
            </>
          )}
        </button>

        <button
          onClick={() => generateDrafts(true)}
          disabled={leadIds.length === 0 || generatingStatus === 'loading'}
          className={cn(
            'inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold',
            'bg-primary text-on-primary shadow-ambient-sm transition-colors',
            'hover:bg-primary/90',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
        >
          Genera e invia tutto
        </button>
      </div>

      {/* Draft results */}
      {drafts.length > 0 && (
        <div className="mt-2 space-y-3">
          <p className="text-xs text-on-surface-variant">
            {drafts.filter((d) => d.ok).length} bozze generate,{' '}
            {drafts.filter((d) => !d.ok).length} non riuscite
          </p>
          <div className="max-h-[480px] overflow-y-auto rounded-xl border border-outline-variant/30">
            {drafts.map((draft, idx) => (
              <DraftRow
                key={draft.lead_id}
                draft={draft}
                idx={idx}
                onSend={() => sendDraft(idx)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function NumberSlider({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (n: number) => void;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <label className="text-xs font-medium text-on-surface-variant">
          {label}
        </label>
        <span className="font-mono text-xs font-semibold text-on-surface">
          {value}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-primary"
      />
    </div>
  );
}

function DraftRow({
  draft,
  idx: _idx,
  onSend,
}: {
  draft: DraftState;
  idx: number;
  onSend: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      className={cn(
        'border-b border-outline-variant/20 px-4 py-3 last:border-0',
        !draft.ok && 'bg-error-container/10',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-xs text-on-surface-variant">
            {draft.lead_id}
          </p>
          {draft.ok ? (
            <p className="mt-0.5 truncate text-sm font-medium text-on-surface">
              {draft.subject ?? '(no subject)'}
            </p>
          ) : (
            <p className="mt-0.5 text-xs text-error">{draft.error}</p>
          )}
        </div>

        {draft.ok && (
          <div className="flex shrink-0 items-center gap-2">
            {draft.sendStatus === 'sent' ? (
              <span className="text-xs text-primary">Inviato</span>
            ) : draft.sendStatus === 'error' ? (
              <span className="text-xs text-error" title={draft.sendError}>
                Errore invio
              </span>
            ) : (
              <button
                onClick={onSend}
                disabled={draft.sendStatus === 'sending'}
                className={cn(
                  'rounded-md px-3 py-1 text-xs font-semibold',
                  'bg-primary text-on-primary hover:bg-primary/90',
                  'disabled:opacity-50',
                )}
              >
                {draft.sendStatus === 'sending' ? 'Invio…' : 'Invia'}
              </button>
            )}
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-xs text-on-surface-variant hover:text-on-surface"
            >
              {expanded ? 'Chiudi' : 'Anteprima'}
            </button>
            <Link
              href={`/leads/${draft.lead_id}`}
              className="text-xs text-on-surface-variant hover:text-primary hover:underline"
            >
              Apri
            </Link>
          </div>
        )}
      </div>

      {expanded && draft.ok && draft.body && (
        <pre className="mt-3 whitespace-pre-wrap rounded-lg bg-surface-container-low p-3 font-sans text-xs text-on-surface leading-relaxed">
          {draft.body}
        </pre>
      )}
    </div>
  );
}

function SparkleIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 3l1.9 5.6L19 10.7l-5.1.9L12 17l-1.9-5.4L5 10.7l5.1-.9z" />
      <path d="M5 3v4M19 17v4M3 5h4M17 19h4" />
    </svg>
  );
}

function SpinnerIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="animate-spin"
    >
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  );
}
