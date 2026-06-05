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

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import {
  AlertTriangle,
  ChevronDown,
  Clock,
  Eye,
  EyeOff,
  Flame,
  Inbox,
  Loader2,
  MailCheck,
  RefreshCw,
  Send,
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
  // Engagement signals — drive the contatto/lead classification below.
  outreach_sent_at: string | null;
  outreach_delivered_at: string | null;
  outreach_opened_at: string | null;
  outreach_clicked_at: string | null;
  outreach_replied_at: string | null;
  whatsapp_initiated_at: string | null;
  dashboard_visited_at: string | null;
  last_portal_event_at: string | null;
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

interface ActivityEvent {
  event_type: string;
  event_source: string | null;
  occurred_at: string | null;
  payload: Record<string, unknown> | null;
}

interface PortalEvent {
  event_kind: string;
  occurred_at: string | null;
  metadata: Record<string, unknown> | null;
}

interface LeadActivity {
  lead_id: string;
  events: ActivityEvent[];
  portal_events: PortalEvent[];
}

type ReviewStatus = 'pending' | 'released' | 'held';

/** Italian labels for the raw event_type / portal event_kind codes. */
const ACTIVITY_LABELS: Record<string, string> = {
  'lead.outreach_sent': 'Email inviata',
  'lead.email_delivered': 'Email consegnata',
  'lead.email_opened': 'Email aperta',
  'lead.email_clicked': 'Link cliccato',
  'lead.portal_visited': 'Ha aperto la pagina personale',
  'lead.whatsapp_click': 'Click su WhatsApp',
  'lead.appointment_requested': 'Richiesta di contatto',
  'lead.bolletta_uploaded': 'Bolletta caricata',
  'lead.optout_requested': 'Disiscrizione',
  'lead.rendered': 'Rendering generato',
  'lead.scored': 'Punteggio calcolato',
  'portal.view': 'Apertura pagina',
  'portal.scroll_50': 'Letto a metà',
  'portal.scroll_90': 'Letto fino in fondo',
  'portal.roi_viewed': 'Ha visto le stime ROI',
  'portal.video_play': 'Ha avviato il video',
  'portal.video_complete': 'Ha guardato tutto il video',
  'portal.whatsapp_click': 'Click su WhatsApp',
  'portal.appointment_click': 'Click richiedi sopralluogo',
  'portal.bolletta_uploaded': 'Bolletta caricata',
};

function activityLabel(code: string): string {
  return ACTIVITY_LABELS[code] ?? code.replace(/^(lead|portal)\./, '').replace(/_/g, ' ');
}

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

// Pipeline statuses that, on their own, mean the prospect has reacted.
// Matches the dashboard's "Lead = contatto che ha reagito" definition.
const REACTED_STATUSES = new Set([
  'clicked',
  'engaged',
  'to_call',
  'whatsapp',
  'appointment',
  'closed_won',
]);

/**
 * A row is a **lead** (vs a pre-engagement **contatto**) when the prospect
 * has reacted: clicked the CTA, visited the portal, replied, started a
 * WhatsApp chat, or moved to a reacted pipeline stage. A plain open or a
 * delivered/sent email is NOT a reaction — that stays a contatto.
 */
function isReactedLead(l: PendingLead): boolean {
  if (
    l.outreach_clicked_at ||
    l.outreach_replied_at ||
    l.whatsapp_initiated_at ||
    l.dashboard_visited_at ||
    l.last_portal_event_at
  ) {
    return true;
  }
  return l.pipeline_status != null && REACTED_STATUSES.has(l.pipeline_status);
}

type StageTone = 'neutral' | 'info' | 'hot';

/**
 * Furthest stage the prospect reached, for a single status chip. Ordered
 * from coldest (in coda) to hottest (appuntamento) — the last matching
 * signal wins.
 */
function leadStage(l: PendingLead): { label: string; tone: StageTone } {
  if (l.pipeline_status === 'appointment' || l.pipeline_status === 'closed_won') {
    return { label: 'Appuntamento', tone: 'hot' };
  }
  if (l.whatsapp_initiated_at || l.pipeline_status === 'whatsapp') {
    return { label: 'WhatsApp', tone: 'hot' };
  }
  if (l.outreach_replied_at) return { label: 'Ha risposto', tone: 'hot' };
  if (l.dashboard_visited_at || l.last_portal_event_at) {
    return { label: 'Ha visitato il portale', tone: 'hot' };
  }
  if (l.outreach_clicked_at || l.pipeline_status === 'clicked') {
    return { label: 'Ha cliccato', tone: 'hot' };
  }
  if (l.outreach_opened_at || l.pipeline_status === 'opened') {
    return { label: 'Aperta', tone: 'info' };
  }
  if (l.outreach_delivered_at || l.pipeline_status === 'delivered') {
    return { label: 'Consegnata', tone: 'info' };
  }
  if (l.outreach_sent_at || l.pipeline_status === 'sent') {
    return { label: 'Inviata', tone: 'info' };
  }
  return { label: 'In coda', tone: 'neutral' };
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

      <SendNowButton tenantId={tenantId} />
      <RegenRendersButton tenantId={tenantId} />
      <RecheckExistingPvButton tenantId={tenantId} />
      <VisionSelfTestButton />
      <LeadQueue tenantId={tenantId} />
      <InboundQueue tenantId={tenantId} />
    </div>
  );
}

function SendNowButton({ tenantId }: { tenantId: string }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (busy) return;
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      const res = await api.post<{ ok: boolean; picked: number; cap: number }>(
        `/v1/admin/trial/run-daily-send?tenant_id=${encodeURIComponent(tenantId)}`,
        {},
      );
      setResult(
        res.picked > 0
          ? `${res.picked} lead presi dal magazzino (cap ${res.cap}/giorno). Render + invio in corso, nelle finestre orarie 08–12 / 14–18.`
          : `Nessun lead pronto da inviare adesso (magazzino vuoto o cap giornaliero già raggiunto).`,
      );
    } catch (e) {
      setError(errMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <BentoCard span="full">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Send size={16} strokeWidth={2.25} aria-hidden className="text-primary" />
          <h2 className="font-headline text-lg font-bold tracking-tight text-on-surface">
            Avvia invii ora
          </h2>
        </div>
        <button
          type="button"
          onClick={() => void run()}
          disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {busy ? (
            <Loader2 size={14} strokeWidth={2.25} aria-hidden className="animate-spin" />
          ) : (
            <Send size={14} strokeWidth={2.25} aria-hidden />
          )}
          Avvia invii ora
        </button>
      </div>
      <p className="mt-1 text-xs text-on-surface-variant">
        Preleva i lead pronti dal magazzino (fino al cap giornaliero), genera i
        render e parte l&apos;outreach — senza attendere il giro automatico delle
        07:30. Rispetta cap e finestre orarie; non forza invii fuori orario.
      </p>
      {result && (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-primary/30 bg-primary/10 px-3 py-2 text-sm text-on-surface">
          <MailCheck size={14} strokeWidth={2.25} aria-hidden className="mt-0.5 shrink-0 text-primary" />
          <span>{result}</span>
        </div>
      )}
      {error && (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-error/30 bg-error-container/20 px-3 py-2 text-sm text-error">
          <AlertTriangle size={14} strokeWidth={2.25} aria-hidden className="mt-0.5 shrink-0" />
          <span className="whitespace-pre-wrap">{error}</span>
        </div>
      )}
    </BentoCard>
  );
}

function RegenRendersButton({ tenantId }: { tenantId: string }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(allLeads: boolean) {
    if (busy) return;
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      const qs = `tenant_id=${encodeURIComponent(tenantId)}${allLeads ? '&all=true' : ''}`;
      const res = await api.post<{ ok: boolean; regenerated: number }>(
        `/v1/admin/trial/regenerate-failed-renders?${qs}`,
        {},
      );
      setResult(
        res.regenerated > 0
          ? `${res.regenerated} render rigenerati${allLeads ? ' (TUTTI i lead, anche quelli già fatti)' : ' (solo i falliti)'}. Il render comparirà nel dettaglio tra ~1-2 min. Nessun invio rifatto.`
          : allLeads
            ? `Nessun lead da rigenerare.`
            : `Nessun render fallito da rigenerare.`,
      );
    } catch (e) {
      setError(errMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <BentoCard span="full">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <RefreshCw size={16} strokeWidth={2.25} aria-hidden className="text-primary" />
          <h2 className="font-headline text-lg font-bold tracking-tight text-on-surface">
            Rigenera render
          </h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void run(false)}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-primary/40 px-4 py-2 text-sm font-semibold text-primary transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {busy ? (
              <Loader2 size={14} strokeWidth={2.25} aria-hidden className="animate-spin" />
            ) : (
              <RefreshCw size={14} strokeWidth={2.25} aria-hidden />
            )}
            Solo falliti
          </button>
          <button
            type="button"
            onClick={() => void run(true)}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {busy ? (
              <Loader2 size={14} strokeWidth={2.25} aria-hidden className="animate-spin" />
            ) : (
              <RefreshCw size={14} strokeWidth={2.25} aria-hidden />
            )}
            Rigenera TUTTI
          </button>
        </div>
      </div>
      <p className="mt-1 text-xs text-on-surface-variant">
        <strong>Solo falliti</strong>: re-renderizza i lead il cui render era
        fallito (buchi Solar/glitch). <strong>Rigenera TUTTI</strong>: rifà
        ogni render, anche quelli già riusciti — serve ad applicare le
        correzioni di centraggio/zoom a hotel e complessi già renderizzati
        (~1 chiamata Solar per lead). In nessun caso rimanda email.
      </p>
      {result && (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-primary/30 bg-primary/10 px-3 py-2 text-sm text-on-surface">
          <MailCheck size={14} strokeWidth={2.25} aria-hidden className="mt-0.5 shrink-0 text-primary" />
          <span>{result}</span>
        </div>
      )}
      {error && (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-error/30 bg-error-container/20 px-3 py-2 text-sm text-error">
          <AlertTriangle size={14} strokeWidth={2.25} aria-hidden className="mt-0.5 shrink-0" />
          <span className="whitespace-pre-wrap">{error}</span>
        </div>
      )}
    </BentoCard>
  );
}

function RecheckExistingPvButton({ tenantId }: { tenantId: string }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (busy) return;
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      const res = await api.post<{
        ok: boolean;
        checked: number;
        verified_ok: number;
        not_verifiable: number;
        blacklisted_existing_pv: number;
      }>(
        `/v1/admin/trial/recheck-existing-pv?tenant_id=${encodeURIComponent(tenantId)}`,
        {},
      );
      const warn =
        res.not_verifiable > 0
          ? ` · ⚠️ ${res.not_verifiable} NON verificati (token MAPBOX/ANTHROPIC sull'API?)`
          : '';
      setResult(
        `${res.verified_ok}/${res.checked} verificati col vision · ${res.blacklisted_existing_pv} con impianto già sul tetto → blacklistati${warn}. Nessun invio.`,
      );
    } catch (e) {
      setError(errMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <BentoCard span="full">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <RefreshCw size={16} strokeWidth={2.25} aria-hidden className="text-primary" />
          <h2 className="font-headline text-lg font-bold tracking-tight text-on-surface">
            Ricontrolla impianti esistenti
          </h2>
        </div>
        <button
          type="button"
          onClick={() => void run()}
          disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {busy ? (
            <Loader2 size={14} strokeWidth={2.25} aria-hidden className="animate-spin" />
          ) : (
            <RefreshCw size={14} strokeWidth={2.25} aria-hidden />
          )}
          Ricontrolla
        </button>
      </div>
      <p className="mt-1 text-xs text-on-surface-variant">
        Analisi vision (~0,5¢/lead) sui lead <strong>non ancora inviati</strong>:
        chi ha già i pannelli sul tetto viene messo in blacklist così non parte.
        Non invia nulla.
      </p>
      {result && (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-primary/30 bg-primary/10 px-3 py-2 text-sm text-on-surface">
          <MailCheck size={14} strokeWidth={2.25} aria-hidden className="mt-0.5 shrink-0 text-primary" />
          <span>{result}</span>
        </div>
      )}
      {error && (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-error/30 bg-error-container/20 px-3 py-2 text-sm text-error">
          <AlertTriangle size={14} strokeWidth={2.25} aria-hidden className="mt-0.5 shrink-0" />
          <span className="whitespace-pre-wrap">{error}</span>
        </div>
      )}
    </BentoCard>
  );
}

function VisionSelfTestButton() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [ok, setOk] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (busy) return;
    setBusy(true);
    setResult(null);
    setOk(null);
    setError(null);
    try {
      const res = await api.post<{
        ok: boolean;
        working?: boolean;
        has_existing_pv?: boolean;
        confidence?: number;
        stage?: string;
        hint?: string;
      }>(`/v1/admin/trial/existing-pv-selftest`, {});
      if (res.ok && res.working) {
        setOk(true);
        setResult(
          `✅ Vision OK — riconosce l'impianto noto (La Reggia): has_existing_pv=true · confidenza ${Math.round((res.confidence ?? 0) * 100)}%. La verifica gira davvero sull'API.`,
        );
      } else {
        setOk(false);
        setResult(
          `❌ Vision NON funzionante (stage: ${res.stage ?? '?'}). ${res.hint ?? ''} → finché non è risolto, il recheck e il gate L4 fanno fail-open (lasciano passare).`,
        );
      }
    } catch (e) {
      setError(errMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <BentoCard span="full">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <RefreshCw size={16} strokeWidth={2.25} aria-hidden className="text-primary" />
          <h2 className="font-headline text-lg font-bold tracking-tight text-on-surface">
            Auto-test vision (esistenti-PV)
          </h2>
        </div>
        <button
          type="button"
          onClick={() => void run()}
          disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-lg border border-primary/40 px-4 py-2 text-sm font-semibold text-primary transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {busy ? (
            <Loader2 size={14} strokeWidth={2.25} aria-hidden className="animate-spin" />
          ) : (
            <RefreshCw size={14} strokeWidth={2.25} aria-hidden />
          )}
          Esegui auto-test
        </button>
      </div>
      <p className="mt-1 text-xs text-on-surface-variant">
        Prova il vision su un edificio che <strong>sappiamo</strong> avere
        l&apos;impianto (La Reggia). Se risponde &quot;sì&quot;, la verifica
        funziona davvero sull&apos;ambiente; se no, ti dice quale chiave manca
        sull&apos;API.
      </p>
      {result && (
        <div
          className={`mt-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
            ok
              ? 'border-primary/30 bg-primary/10 text-on-surface'
              : 'border-error/30 bg-error-container/20 text-error'
          }`}
        >
          {ok ? (
            <MailCheck size={14} strokeWidth={2.25} aria-hidden className="mt-0.5 shrink-0 text-primary" />
          ) : (
            <AlertTriangle size={14} strokeWidth={2.25} aria-hidden className="mt-0.5 shrink-0" />
          )}
          <span className="whitespace-pre-wrap">{result}</span>
        </div>
      )}
      {error && (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-error/30 bg-error-container/20 px-3 py-2 text-sm text-error">
          <AlertTriangle size={14} strokeWidth={2.25} aria-hidden className="mt-0.5 shrink-0" />
          <span className="whitespace-pre-wrap">{error}</span>
        </div>
      )}
    </BentoCard>
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
            Coda promozione a lead
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
              {s === 'pending' ? 'Da promuovere' : s === 'held' ? 'Tenuti a contatto' : 'Promossi'}
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

      <p className="mt-1 text-xs text-on-surface-variant">
        Il tenant vede sempre i propri <span className="font-semibold text-on-surface">contatti</span>{' '}
        e le relative schede. Qui compaiono i contatti che hanno{' '}
        <span className="font-semibold text-on-surface">reagito</span> (click, portale,
        WhatsApp, risposta o appuntamento) e attendono la promozione a{' '}
        <span className="font-semibold text-on-surface">lead</span>. «Promuovi a
        lead» fa passare la scheda allo stato lead nella dashboard del tenant;
        finché non la promuovi resta un contatto come gli altri.
      </p>

      {error && (
        <div className="mt-4 flex items-start gap-2 rounded-lg border border-error/30 bg-error-container/20 px-3 py-2 text-sm text-error">
          <AlertTriangle size={14} strokeWidth={2.25} aria-hidden className="mt-0.5 shrink-0" />
          <span className="whitespace-pre-wrap">{error}</span>
        </div>
      )}

      {leads.length === 0 && !loading && !error && (
        <p className="mt-4 rounded-lg bg-surface-container-low px-4 py-8 text-center text-sm text-on-surface-variant">
          Nessun contatto in questo stato.
        </p>
      )}

      <LeadGroup
        title="Lead — hanno reagito"
        icon={<Flame size={14} strokeWidth={2.25} aria-hidden className="text-error" />}
        rows={leads.filter(isReactedLead)}
        reviewStatus={reviewStatus}
        busyId={busyId}
        act={act}
        emptyHint="Nessun lead reattivo: nessun prospect ha ancora cliccato, visitato il portale o risposto."
      />

      <LeadGroup
        title="Contatti — pre-engagement"
        icon={<Send size={14} strokeWidth={2.25} aria-hidden className="text-on-surface-variant" />}
        rows={leads.filter((l) => !isReactedLead(l))}
        reviewStatus={reviewStatus}
        busyId={busyId}
        act={act}
        emptyHint="Nessun contatto pre-engagement in questo stato."
      />
    </BentoCard>
  );
}

function StageChip({ lead }: { lead: PendingLead }) {
  const { label, tone } = leadStage(lead);
  const cls =
    tone === 'hot'
      ? 'bg-error-container/30 text-error'
      : tone === 'info'
        ? 'bg-primary/10 text-primary'
        : 'bg-surface-container text-on-surface-variant';
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${cls}`}>
      {label}
    </span>
  );
}

function LeadGroup({
  title,
  icon,
  rows,
  reviewStatus,
  busyId,
  act,
  emptyHint,
}: {
  title: string;
  icon: ReactNode;
  rows: PendingLead[];
  reviewStatus: ReviewStatus;
  busyId: string | null;
  act: (leadId: string, action: 'release' | 'hold') => void;
  emptyHint: string;
}) {
  return (
    <div className="mt-5">
      <div className="flex items-center gap-2">
        {icon}
        <h3 className="text-xs font-bold uppercase tracking-wider text-on-surface-variant">
          {title}
        </h3>
        <span className="rounded-full bg-surface-container px-2 py-0.5 text-[11px] font-semibold text-on-surface-variant">
          {rows.length}
        </span>
      </div>

      <div className="mt-2 space-y-2">
        {rows.length === 0 ? (
          <p className="rounded-lg bg-surface-container-low px-4 py-4 text-center text-xs text-on-surface-variant">
            {emptyHint}
          </p>
        ) : (
          rows.map((l) => (
            <LeadRow
              key={l.id}
              lead={l}
              reviewStatus={reviewStatus}
              busyId={busyId}
              act={act}
            />
          ))
        )}
      </div>
    </div>
  );
}

function LeadRow({
  lead: l,
  reviewStatus,
  busyId,
  act,
}: {
  lead: PendingLead;
  reviewStatus: ReviewStatus;
  busyId: string | null;
  act: (leadId: string, action: 'release' | 'hold') => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [activity, setActivity] = useState<LeadActivity | null>(null);
  const [loadingAct, setLoadingAct] = useState(false);
  const [actErr, setActErr] = useState<string | null>(null);

  async function toggle() {
    const next = !expanded;
    setExpanded(next);
    if (next && !activity && !loadingAct) {
      setLoadingAct(true);
      setActErr(null);
      try {
        const res = await api.get<LeadActivity>(
          `/v1/admin/trial/leads/${l.id}/activity`,
        );
        setActivity(res);
      } catch (e) {
        setActErr(errMessage(e));
      } finally {
        setLoadingAct(false);
      }
    }
  }

  // Merge events + portal events into one descending timeline.
  const timeline = activity
    ? [
        ...activity.events.map((e) => ({
          code: e.event_type,
          at: e.occurred_at,
          src: e.event_source,
        })),
        ...activity.portal_events.map((p) => ({
          code: p.event_kind,
          at: p.occurred_at,
          src: 'portale',
        })),
      ].sort((a, b) => (b.at ?? '').localeCompare(a.at ?? ''))
    : [];

  return (
    <div className="rounded-lg bg-surface-container-low px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-semibold text-on-surface">
              {l.business_name || '(azienda senza nome)'}
            </p>
            <StageChip lead={l} />
          </div>
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
          <button
            type="button"
            onClick={() => void toggle()}
            aria-expanded={expanded}
            className="inline-flex items-center gap-1.5 rounded-lg bg-surface-container px-3 py-1.5 text-xs font-semibold text-on-surface-variant transition-colors hover:text-on-surface"
          >
            <ChevronDown
              size={12}
              strokeWidth={2.25}
              aria-hidden
              className={`transition-transform ${expanded ? 'rotate-180' : ''}`}
            />
            Dettaglio
          </button>
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
              Promuovi a lead
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
              Tieni come contatto
            </button>
          )}
        </div>
      </div>

      {expanded && (
        <div className="mt-3 border-t border-outline-variant/30 pt-3">
          {loadingAct ? (
            <p className="flex items-center gap-2 text-xs text-on-surface-variant">
              <Loader2 size={12} strokeWidth={2.25} aria-hidden className="animate-spin" />
              Carico l&apos;attività…
            </p>
          ) : actErr ? (
            <p className="text-xs text-error">{actErr}</p>
          ) : timeline.length === 0 ? (
            <p className="text-xs text-on-surface-variant">
              Nessuna attività registrata per questo contatto.
            </p>
          ) : (
            <ol className="space-y-1.5">
              {timeline.map((t, i) => (
                <li key={i} className="flex items-start gap-2 text-xs">
                  <Clock
                    size={12}
                    strokeWidth={2.25}
                    aria-hidden
                    className="mt-0.5 shrink-0 text-on-surface-variant"
                  />
                  <span className="w-28 shrink-0 font-mono text-[11px] text-on-surface-variant">
                    {fmtDate(t.at)}
                  </span>
                  <span className="flex-1 text-on-surface">
                    {activityLabel(t.code)}
                    {t.src ? (
                      <span className="ml-1 text-[11px] text-on-surface-variant">
                        · {t.src}
                      </span>
                    ) : null}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
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
