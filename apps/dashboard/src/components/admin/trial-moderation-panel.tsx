'use client';

/**
 * TrialModerationPanel — super-admin curation surface for a moderated tenant.
 *
 * Talks to the `/v1/admin/trial/*` endpoints (all service-role, RLS-bypassing,
 * gated `_require_super_admin` server-side). Two independent queues:
 *
 *   • Lead queue   — GET /trial/pending-leads?tenant_id=&review_status=pending
 *                    POST /trial/leads/{id}/release  (far comparire)
 *                    POST /trial/leads/{id}/hold     (tieni nascosto)
 *   • Inbound queue — GET /trial/pending-inbound?status=pending&tenant_id=
 *                    POST /trial/inbound/{id}/approve (inoltra al tenant)
 *                    POST /trial/inbound/{id}/reject  (scarta)
 *
 * Everything here is invisible to the tenant; this component only ever renders
 * for an operator whose JWT carries the super_admin claim (the page 404s
 * otherwise).
 */

import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  Eye,
  EyeOff,
  Inbox,
  Loader2,
  MailCheck,
  RefreshCw,
  Users,
  XCircle,
} from 'lucide-react';

import { BentoCard } from '@/components/ui/bento-card';
import { api } from '@/lib/api-client';

interface PendingLead {
  id: string;
  tenant_id: string;
  operator_review_status: string;
  operator_released_at: string | null;
  pipeline_status: string | null;
  score: number | null;
  score_tier: string | null;
  public_slug: string | null;
  created_at: string | null;
  business_name: string | null;
  address: string | null;
  comune: string | null;
  provincia: string | null;
}

interface PendingLeadsResponse {
  leads: PendingLead[];
  total: number;
}

interface PendingInbound {
  id: string;
  tenant_id: string;
  lead_id: string;
  status: string;
  dossier_url: string | null;
  payload: Record<string, unknown>;
  created_at: string | null;
  decided_at: string | null;
  business_name: string | null;
  public_slug: string | null;
}

interface PendingInboundResponse {
  requests: PendingInbound[];
  total: number;
}

type ReviewStatus = 'pending' | 'released' | 'held';

function errMessage(e: unknown): string {
  const err = e as { message?: string; body?: { detail?: string } };
  return err?.body?.detail ?? err?.message ?? 'Errore sconosciuto';
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('it-IT', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

function payloadField(p: Record<string, unknown>, ...keys: string[]): string | null {
  for (const k of keys) {
    const v = p[k];
    if (typeof v === 'string' && v.trim()) return v;
  }
  return null;
}

export function TrialModerationPanel({ initialTenantId }: { initialTenantId: string }) {
  const [tenantId, setTenantId] = useState(initialTenantId);
  const [tenantInput, setTenantInput] = useState(initialTenantId);

  return (
    <div className="space-y-8">
      {/* Tenant selector — the moderated tenant whose queues we curate. */}
      <div className="flex flex-wrap items-end gap-3 rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-4 py-3">
        <div className="flex-1 min-w-[260px]">
          <label className="block text-xs font-semibold text-on-surface-variant">
            Tenant moderato (UUID)
          </label>
          <input
            value={tenantInput}
            onChange={(e) => setTenantInput(e.target.value)}
            spellCheck={false}
            className="mt-1 w-full rounded-lg border border-outline-variant/40 bg-surface px-3 py-1.5 font-mono text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary/60"
          />
        </div>
        <button
          type="button"
          onClick={() => setTenantId(tenantInput.trim())}
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary transition-opacity hover:opacity-90"
        >
          Carica
        </button>
      </div>

      <LeadQueue tenantId={tenantId} />
      <InboundQueue tenantId={tenantId} />
    </div>
  );
}

function LeadQueue({ tenantId }: { tenantId: string }) {
  const [reviewStatus, setReviewStatus] = useState<ReviewStatus>('pending');
  const [leads, setLeads] = useState<PendingLead[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get<PendingLeadsResponse>(
        `/v1/admin/trial/pending-leads?tenant_id=${encodeURIComponent(tenantId)}&review_status=${reviewStatus}`,
      );
      setLeads(res.leads);
      setTotal(res.total);
    } catch (e) {
      setError(errMessage(e));
      setLeads([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [tenantId, reviewStatus]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(leadId: string, action: 'release' | 'hold') {
    setBusyId(leadId);
    setError(null);
    try {
      await api.post(`/v1/admin/trial/leads/${leadId}/${action}`, {});
      // Drop the row optimistically — it no longer belongs in this filter.
      setLeads((prev) => prev.filter((l) => l.id !== leadId));
      setTotal((t) => Math.max(0, t - 1));
    } catch (e) {
      setError(errMessage(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <BentoCard span="full">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Users size={16} strokeWidth={2.25} aria-hidden className="text-primary" />
          <h2 className="font-headline text-lg font-bold tracking-tight text-on-surface">
            Coda lead
          </h2>
          <span className="rounded-full bg-surface-container px-2 py-0.5 text-xs font-semibold text-on-surface-variant">
            {total}
          </span>
        </div>

        <div className="flex items-center gap-2">
          {(['pending', 'held', 'released'] as ReviewStatus[]).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setReviewStatus(s)}
              className={`rounded-lg px-3 py-1 text-xs font-semibold transition-colors ${
                reviewStatus === s
                  ? 'bg-primary text-on-primary'
                  : 'bg-surface-container text-on-surface-variant hover:text-on-surface'
              }`}
            >
              {s === 'pending' ? 'Da rivedere' : s === 'held' ? 'Nascosti' : 'Rilasciati'}
            </button>
          ))}
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg bg-surface-container px-3 py-1 text-xs font-semibold text-on-surface-variant transition-colors hover:text-on-surface disabled:opacity-50"
          >
            {loading ? (
              <Loader2 size={12} strokeWidth={2.25} aria-hidden className="animate-spin" />
            ) : (
              <RefreshCw size={12} strokeWidth={2.25} aria-hidden />
            )}
            Aggiorna
          </button>
        </div>
      </div>

      {error && (
        <div className="mt-4 flex items-start gap-2 rounded-lg border border-error/30 bg-error-container/20 px-3 py-2 text-sm text-error">
          <AlertTriangle size={14} strokeWidth={2.25} aria-hidden className="mt-0.5 shrink-0" />
          <span className="whitespace-pre-wrap">{error}</span>
        </div>
      )}

      <div className="mt-4 space-y-2">
        {leads.length === 0 && !loading && !error && (
          <p className="rounded-lg bg-surface-container-low px-4 py-8 text-center text-sm text-on-surface-variant">
            Nessun lead in questo stato.
          </p>
        )}

        {leads.map((l) => (
          <div
            key={l.id}
            className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-surface-container-low px-4 py-3"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-on-surface">
                {l.business_name || '(azienda senza nome)'}
              </p>
              <p className="truncate text-xs text-on-surface-variant">
                {[l.address, l.comune, l.provincia].filter(Boolean).join(', ') || '—'}
              </p>
              <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 font-mono text-[11px] text-on-surface-variant">
                <span>
                  score: <span className="text-on-surface">{l.score ?? '—'}</span>
                  {l.score_tier ? ` (${l.score_tier})` : ''}
                </span>
                <span>stato: {l.pipeline_status ?? '—'}</span>
                <span>{fmtDate(l.created_at)}</span>
              </p>
            </div>

            <div className="flex shrink-0 items-center gap-2">
              {reviewStatus !== 'released' && (
                <button
                  type="button"
                  onClick={() => void act(l.id, 'release')}
                  disabled={busyId === l.id}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
                >
                  {busyId === l.id ? (
                    <Loader2 size={12} strokeWidth={2.25} aria-hidden className="animate-spin" />
                  ) : (
                    <Eye size={12} strokeWidth={2.25} aria-hidden />
                  )}
                  Far comparire
                </button>
              )}
              {reviewStatus !== 'held' && (
                <button
                  type="button"
                  onClick={() => void act(l.id, 'hold')}
                  disabled={busyId === l.id}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-surface-container px-3 py-1.5 text-xs font-semibold text-on-surface-variant transition-colors hover:text-on-surface disabled:opacity-50"
                >
                  {busyId === l.id ? (
                    <Loader2 size={12} strokeWidth={2.25} aria-hidden className="animate-spin" />
                  ) : (
                    <EyeOff size={12} strokeWidth={2.25} aria-hidden />
                  )}
                  Tieni nascosto
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </BentoCard>
  );
}

function InboundQueue({ tenantId }: { tenantId: string }) {
  const [requests, setRequests] = useState<PendingInbound[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get<PendingInboundResponse>(
        `/v1/admin/trial/pending-inbound?status=pending&tenant_id=${encodeURIComponent(tenantId)}`,
      );
      setRequests(res.requests);
      setTotal(res.total);
    } catch (e) {
      setError(errMessage(e));
      setRequests([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(requestId: string, action: 'approve' | 'reject') {
    setBusyId(requestId);
    setError(null);
    try {
      await api.post(`/v1/admin/trial/inbound/${requestId}/${action}`, {});
      setRequests((prev) => prev.filter((r) => r.id !== requestId));
      setTotal((t) => Math.max(0, t - 1));
    } catch (e) {
      setError(errMessage(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <BentoCard span="full">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Inbox size={16} strokeWidth={2.25} aria-hidden className="text-primary" />
          <h2 className="font-headline text-lg font-bold tracking-tight text-on-surface">
            Coda inbound
          </h2>
          <span className="rounded-full bg-surface-container px-2 py-0.5 text-xs font-semibold text-on-surface-variant">
            {total}
          </span>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex items-center gap-1.5 rounded-lg bg-surface-container px-3 py-1 text-xs font-semibold text-on-surface-variant transition-colors hover:text-on-surface disabled:opacity-50"
        >
          {loading ? (
            <Loader2 size={12} strokeWidth={2.25} aria-hidden className="animate-spin" />
          ) : (
            <RefreshCw size={12} strokeWidth={2.25} aria-hidden />
          )}
          Aggiorna
        </button>
      </div>

      <p className="mt-1 text-xs text-on-surface-variant">
        Richieste appuntamento dei prospect trattenute prima di raggiungere il
        tenant. «Approva» inoltra mail + webhook + evento e rende visibile il
        lead; «Rifiuta» scarta senza alcuna traccia per il tenant.
      </p>

      {error && (
        <div className="mt-4 flex items-start gap-2 rounded-lg border border-error/30 bg-error-container/20 px-3 py-2 text-sm text-error">
          <AlertTriangle size={14} strokeWidth={2.25} aria-hidden className="mt-0.5 shrink-0" />
          <span className="whitespace-pre-wrap">{error}</span>
        </div>
      )}

      <div className="mt-4 space-y-2">
        {requests.length === 0 && !loading && !error && (
          <p className="rounded-lg bg-surface-container-low px-4 py-8 text-center text-sm text-on-surface-variant">
            Nessuna richiesta inbound in attesa.
          </p>
        )}

        {requests.map((r) => {
          const name = payloadField(r.payload, 'name', 'contact_name', 'full_name');
          const email = payloadField(r.payload, 'email', 'contact_email');
          const phone = payloadField(r.payload, 'phone', 'telefono', 'contact_phone');
          const message = payloadField(r.payload, 'message', 'note', 'messaggio');
          return (
            <div
              key={r.id}
              className="flex flex-wrap items-start justify-between gap-3 rounded-lg bg-surface-container-low px-4 py-3"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-on-surface">
                  {r.business_name || '(azienda senza nome)'}
                </p>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-on-surface-variant">
                  {name && <span>{name}</span>}
                  {email && <span className="font-mono">{email}</span>}
                  {phone && <span className="font-mono">{phone}</span>}
                  <span>{fmtDate(r.created_at)}</span>
                </p>
                {message && (
                  <p className="mt-1 max-w-2xl text-xs italic text-on-surface-variant">
                    «{message}»
                  </p>
                )}
                {r.dossier_url && (
                  <a
                    href={r.dossier_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-1 inline-block text-xs font-semibold text-primary hover:underline"
                  >
                    Apri dossier →
                  </a>
                )}
              </div>

              <div className="flex shrink-0 items-center gap-2">
                <button
                  type="button"
                  onClick={() => void act(r.id, 'approve')}
                  disabled={busyId === r.id}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
                >
                  {busyId === r.id ? (
                    <Loader2 size={12} strokeWidth={2.25} aria-hidden className="animate-spin" />
                  ) : (
                    <MailCheck size={12} strokeWidth={2.25} aria-hidden />
                  )}
                  Approva
                </button>
                <button
                  type="button"
                  onClick={() => void act(r.id, 'reject')}
                  disabled={busyId === r.id}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-surface-container px-3 py-1.5 text-xs font-semibold text-on-surface-variant transition-colors hover:text-on-surface disabled:opacity-50"
                >
                  {busyId === r.id ? (
                    <Loader2 size={12} strokeWidth={2.25} aria-hidden className="animate-spin" />
                  ) : (
                    <XCircle size={12} strokeWidth={2.25} aria-hidden />
                  )}
                  Rifiuta
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </BentoCard>
  );
}
