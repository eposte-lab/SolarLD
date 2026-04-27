'use client';

/**
 * /settings/email-domains — Multi-domain outreach management (Sprint 6.2 + 6.5)
 *
 * Lists all tenant_email_domains rows.  Each domain shows:
 *  • Purpose badge: "brand" (transactional via Resend) or "outreach" (Gmail / cold)
 *  • DNS verification semaphore: SPF · DKIM · DMARC · Tracking
 *  • Suspension banner when paused (alarm, bounce spike, etc.)
 *  • Live "Verifica DNS" button that calls POST /v1/email-domains/{id}/dns-check
 *  • Pause / Un-pause (admin only)
 *  • Add domain modal
 */

import {
  AlertOctagon,
  AlertTriangle,
  Check,
  CheckCircle2,
  XCircle,
} from 'lucide-react';
import { useEffect, useState, useTransition } from 'react';

import { apiClient } from '@/lib/api-client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface RecordStatus {
  ok: boolean;
  found: boolean;
  value: string | null;
  expected: string | null;
  error: string | null;
}

interface DnsVerificationResult {
  domain: string;
  spf: RecordStatus;
  dkim_resend: RecordStatus;
  dkim_google: RecordStatus;
  dmarc: RecordStatus;
  tracking_cname: RecordStatus;
  dmarc_policy: string | null;
}

interface EmailDomainRow {
  id: string;
  domain: string;
  purpose: 'brand' | 'outreach';
  default_provider: string;
  tracking_host: string | null;
  verified_at: string | null;
  spf_verified_at: string | null;
  dkim_verified_at: string | null;
  dmarc_verified_at: string | null;
  tracking_cname_verified_at: string | null;
  dmarc_policy: string | null;
  daily_soft_cap: number;
  paused_until: string | null;
  pause_reason: string | null;
  alarm_bounce: boolean;
  alarm_complaint: boolean;
  active: boolean;
  last_dns_check_at: string | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function EmailDomainsPage() {
  const [domains, setDomains] = useState<EmailDomainRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [checkingId, setCheckingId] = useState<string | null>(null);
  const [dnsResults, setDnsResults] = useState<Record<string, DnsVerificationResult>>({});
  const [isPending, startTransition] = useTransition();

  async function refresh() {
    try {
      const res = await apiClient.get<{ domains: EmailDomainRow[] }>('/v1/email-domains');
      setDomains(res.domains ?? []);
    } catch {
      setError('Errore nel caricamento dei domini');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []);

  async function handleDnsCheck(domainId: string) {
    setCheckingId(domainId);
    try {
      const result = await apiClient.post<DnsVerificationResult>(
        `/v1/email-domains/${domainId}/dns-check`, {}
      );
      setDnsResults((prev) => ({ ...prev, [domainId]: result }));
      await refresh();
    } finally {
      setCheckingId(null);
    }
  }

  async function handleUnpause(domainId: string) {
    startTransition(async () => {
      await apiClient.post(`/v1/email-domains/${domainId}/unpause`, {});
      await refresh();
    });
  }

  async function handleDelete(domainId: string, domain: string) {
    if (!confirm(`Eliminare il dominio ${domain}? Le inbox associate perderanno il collegamento.`)) return;
    startTransition(async () => {
      await apiClient.delete(`/v1/email-domains/${domainId}`);
      await refresh();
    });
  }

  const brandDomains = domains.filter((d) => d.purpose === 'brand');
  const outreachDomains = domains.filter((d) => d.purpose === 'outreach');

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <header className="flex items-end justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Impostazioni · Deliverability
          </p>
          <h1 className="mt-1 font-headline text-4xl font-bold tracking-tighter">
            Domini email
          </h1>
          <p className="mt-2 max-w-xl text-sm text-on-surface-variant">
            Gestisci i domini per l&apos;outreach cold (Gmail OAuth) e per le
            comunicazioni di brand (Resend). Ogni dominio ha la propria
            reputazione, record DNS e tracking host.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowAddModal(true)}
          className="rounded-full bg-gradient-primary px-5 py-2.5 text-sm font-bold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-90"
        >
          + Aggiungi dominio
        </button>
      </header>

      {loading ? (
        <p className="text-sm text-on-surface-variant">Caricamento…</p>
      ) : error ? (
        <p className="text-sm text-error">{error}</p>
      ) : (
        <>
          {/* Brand domains */}
          {brandDomains.length > 0 && (
            <section>
              <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                Dominio di brand · transactional
              </h2>
              <div className="space-y-3">
                {brandDomains.map((d) => (
                  <DomainCard
                    key={d.id}
                    domain={d}
                    dnsResult={dnsResults[d.id] ?? null}
                    isChecking={checkingId === d.id}
                    isPending={isPending}
                    onDnsCheck={() => handleDnsCheck(d.id)}
                    onUnpause={() => handleUnpause(d.id)}
                    onDelete={() => handleDelete(d.id, d.domain)}
                  />
                ))}
              </div>
            </section>
          )}

          {/* Outreach domains */}
          {outreachDomains.length > 0 ? (
            <section>
              <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                Domini outreach · cold B2B (Gmail)
              </h2>
              <div className="space-y-3">
                {outreachDomains.map((d) => (
                  <DomainCard
                    key={d.id}
                    domain={d}
                    dnsResult={dnsResults[d.id] ?? null}
                    isChecking={checkingId === d.id}
                    isPending={isPending}
                    onDnsCheck={() => handleDnsCheck(d.id)}
                    onUnpause={() => handleUnpause(d.id)}
                    onDelete={() => handleDelete(d.id, d.domain)}
                  />
                ))}
              </div>
            </section>
          ) : (
            <section>
              <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
                Domini outreach · cold B2B (Gmail)
              </h2>
              <div className="rounded-xl border border-dashed border-outline-variant/60 px-6 py-10 text-center">
                <p className="font-headline text-lg font-bold text-on-surface">
                  Nessun dominio outreach ancora
                </p>
                <p className="mt-2 text-sm text-on-surface-variant">
                  Aggiungi 1-2 domini dedicati all&apos;outreach cold (diversi dal brand).
                  Ogni dominio avrà 3 inbox Gmail per un totale di ~300 email/giorno.
                </p>
                <button
                  type="button"
                  onClick={() => setShowAddModal(true)}
                  className="mt-5 rounded-full border border-primary px-5 py-2 text-sm font-semibold text-primary hover:bg-primary/10"
                >
                  Aggiungi dominio outreach
                </button>
              </div>
            </section>
          )}
        </>
      )}

      {showAddModal && (
        <AddDomainModal
          onClose={() => setShowAddModal(false)}
          onCreated={async () => { setShowAddModal(false); await refresh(); }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// DomainCard
// ---------------------------------------------------------------------------

function DomainCard({
  domain,
  dnsResult,
  isChecking,
  isPending,
  onDnsCheck,
  onUnpause,
  onDelete,
}: {
  domain: EmailDomainRow;
  dnsResult: DnsVerificationResult | null;
  isChecking: boolean;
  isPending: boolean;
  onDnsCheck: () => void;
  onUnpause: () => void;
  onDelete: () => void;
}) {
  const isSuspended =
    domain.paused_until && new Date(domain.paused_until) > new Date();

  // Derive semaphore flags from persisted timestamps OR live dns check result.
  const spfOk = dnsResult ? dnsResult.spf.ok : !!domain.spf_verified_at;
  const dkimOk = dnsResult
    ? dnsResult.dkim_resend.ok || dnsResult.dkim_google.ok
    : !!domain.dkim_verified_at;
  const dmarcOk = dnsResult ? dnsResult.dmarc.ok : !!domain.dmarc_verified_at;
  const trackingOk = dnsResult
    ? dnsResult.tracking_cname.ok
    : !!domain.tracking_cname_verified_at;

  const allDnsOk = spfOk && dkimOk && dmarcOk;

  return (
    <div
      className={`rounded-xl border px-5 py-4 ${
        isSuspended
          ? 'border-error/40 bg-error-container/10'
          : 'border-outline-variant/40 bg-surface-container-lowest'
      }`}
    >
      {/* Suspension banner */}
      {isSuspended && (
        <div className="mb-4 flex items-start gap-3 rounded-lg bg-error-container px-4 py-3">
          <AlertOctagon
            size={16}
            strokeWidth={2}
            className="mt-0.5 shrink-0 text-error"
            aria-hidden
          />
          <div className="flex-1 text-sm text-on-error-container">
            <span className="font-bold">Dominio sospeso</span> fino al{' '}
            {new Date(domain.paused_until!).toLocaleString('it-IT', {
              day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
            })}.{' '}
            {domain.pause_reason && (
              <span>Motivo: <span className="font-mono">{domain.pause_reason.replace(/_/g, ' ')}</span>. </span>
            )}
            {(domain.alarm_bounce || domain.alarm_complaint) && (
              <span>
                {domain.alarm_bounce && 'Bounce rate elevato. '}
                {domain.alarm_complaint && 'Complaint rate elevato. '}
              </span>
            )}
            Contatta il supporto o{' '}
            <button
              type="button"
              onClick={onUnpause}
              disabled={isPending}
              className="underline hover:no-underline disabled:opacity-50"
            >
              sblocca manualmente
            </button>
            .
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          {/* Domain + badges */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm font-semibold text-on-surface">
              {domain.domain}
            </span>
            <PurposeBadge purpose={domain.purpose} />
            <ProviderBadge provider={domain.default_provider} />
            {!domain.active && (
              <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
                Disattivo
              </span>
            )}
            {allDnsOk && (
              <span className="inline-flex items-center gap-1 rounded-full bg-primary-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-primary-container">
                <Check size={10} strokeWidth={2.75} aria-hidden />
                DNS
              </span>
            )}
          </div>

          {/* Tracking host */}
          {domain.tracking_host && (
            <p className="mt-1 inline-flex items-center gap-1.5 text-xs text-on-surface-variant">
              <span>
                Tracking host:{' '}
                <span className="font-mono">{domain.tracking_host}</span>
              </span>
              {trackingOk ? (
                <CheckCircle2
                  size={12}
                  strokeWidth={2}
                  className="text-primary"
                  aria-label="Verificato"
                />
              ) : (
                <span className="inline-flex items-center gap-1 text-warning">
                  <AlertTriangle size={12} strokeWidth={2} aria-hidden />
                  CNAME non configurato
                </span>
              )}
            </p>
          )}

          {/* DNS semaphore */}
          <div className="mt-3 flex flex-wrap gap-3">
            <DnsDot label="SPF" ok={spfOk} />
            <DnsDot label="DKIM" ok={dkimOk} />
            <DnsDot label="DMARC" ok={dmarcOk} />
            {domain.tracking_host && <DnsDot label="Tracking" ok={trackingOk} />}
          </div>

          {/* DMARC policy nudge */}
          {dmarcOk && dnsResult?.dmarc_policy === 'none' && (
            <p className="mt-2 inline-flex items-start gap-1.5 text-xs text-warning">
              <AlertTriangle
                size={12}
                strokeWidth={2}
                className="mt-0.5 shrink-0"
                aria-hidden
              />
              <span>
                DMARC policy è <span className="font-mono">none</span> — passa a{' '}
                <span className="font-mono">quarantine</span> dopo 14 giorni clean per
                massimizzare la deliverability.
              </span>
            </p>
          )}

          {/* Last DNS check */}
          {domain.last_dns_check_at && (
            <p className="mt-2 text-[11px] text-on-surface-variant">
              Ultima verifica:{' '}
              {new Date(domain.last_dns_check_at).toLocaleString('it-IT', {
                day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
              })}
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="flex shrink-0 flex-col items-end gap-2">
          <button
            type="button"
            onClick={onDnsCheck}
            disabled={isChecking || isPending}
            className="rounded-lg border border-primary px-3 py-1.5 text-xs font-semibold text-primary hover:bg-primary/10 disabled:opacity-50"
          >
            {isChecking ? 'Verifica…' : 'Verifica DNS'}
          </button>
          {domain.purpose === 'outreach' && (
            <button
              type="button"
              onClick={onDelete}
              disabled={isPending}
              className="rounded-lg border border-error/40 px-3 py-1.5 text-xs font-semibold text-error/80 hover:bg-error/10 disabled:opacity-50"
            >
              Elimina
            </button>
          )}
        </div>
      </div>

      {/* Live DNS detail (after check) */}
      {dnsResult && (
        <div className="mt-4 rounded-lg bg-surface-container-low p-4">
          <p className="mb-3 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Risultato verifica DNS live
          </p>
          <div className="space-y-2 text-xs">
            <DnsDetailRow label="SPF" rec={dnsResult.spf} />
            <DnsDetailRow label="DKIM (Resend)" rec={dnsResult.dkim_resend} />
            <DnsDetailRow label="DKIM (Google)" rec={dnsResult.dkim_google} />
            <DnsDetailRow label="DMARC" rec={dnsResult.dmarc} />
            {dnsResult.tracking_cname.found !== false && (
              <DnsDetailRow label="Tracking CNAME" rec={dnsResult.tracking_cname} />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small sub-components
// ---------------------------------------------------------------------------

function PurposeBadge({ purpose }: { purpose: 'brand' | 'outreach' }) {
  return purpose === 'brand' ? (
    <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
      Brand
    </span>
  ) : (
    <span className="rounded-full bg-secondary-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-secondary-container">
      Outreach
    </span>
  );
}

function ProviderBadge({ provider }: { provider: string }) {
  const labels: Record<string, string> = {
    resend: 'Resend',
    gmail_oauth: 'Gmail',
    m365_oauth: 'M365',
    smtp: 'SMTP',
  };
  return (
    <span className="rounded-full border border-outline-variant/50 px-2 py-0.5 text-[10px] font-mono text-on-surface-variant">
      {labels[provider] ?? provider}
    </span>
  );
}

function DnsDot({ label, ok }: { label: string; ok: boolean }) {
  return (
    <span
      className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold ${
        ok
          ? 'bg-primary-container/60 text-on-primary-container'
          : 'bg-error-container/60 text-on-error-container'
      }`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${ok ? 'bg-primary' : 'bg-error/70'}`}
        aria-hidden
      />
      {label}
    </span>
  );
}

function DnsDetailRow({ label, rec }: { label: string; rec: RecordStatus }) {
  const Icon = rec.ok ? CheckCircle2 : rec.found ? AlertTriangle : XCircle;
  const color = rec.ok ? 'text-primary' : rec.found ? 'text-warning' : 'text-error';
  return (
    <div className="flex items-start gap-2">
      <Icon
        size={14}
        strokeWidth={2}
        className={`mt-0.5 shrink-0 ${color}`}
        aria-hidden
      />
      <div className="flex-1">
        <span className="font-semibold text-on-surface">{label}</span>
        {rec.value && (
          <span className="ml-2 font-mono text-on-surface-variant">
            {rec.value.length > 80 ? rec.value.slice(0, 80) + '…' : rec.value}
          </span>
        )}
        {rec.error && (
          <span className="ml-2 text-error">{rec.error}</span>
        )}
        {!rec.ok && rec.expected && (
          <p className="mt-0.5 text-on-surface-variant">
            Atteso: <span className="font-mono">{rec.expected}</span>
          </p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// AddDomainModal
// ---------------------------------------------------------------------------

function AddDomainModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => Promise<void>;
}) {
  const [domain, setDomain] = useState('');
  const [purpose, setPurpose] = useState<'outreach' | 'brand'>('outreach');
  const [trackingHost, setTrackingHost] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Auto-suggest tracking host as "go.{domain}"
  function handleDomainChange(val: string) {
    setDomain(val);
    if (val.trim() && !trackingHost) {
      setTrackingHost(`go.${val.trim()}`);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await apiClient.post('/v1/email-domains', {
        domain: domain.trim().toLowerCase(),
        purpose,
        tracking_host: trackingHost.trim() || null,
        default_provider: purpose === 'outreach' ? 'gmail_oauth' : 'resend',
      });
      await onCreated();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Errore nella creazione');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-full max-w-md rounded-2xl bg-surface p-6 shadow-2xl">
        <h2 className="font-headline text-xl font-bold tracking-tighter">
          Aggiungi dominio email
        </h2>
        <p className="mt-1 text-sm text-on-surface-variant">
          Utilizza un dominio che possiedi e su cui puoi configurare i record DNS.
        </p>

        <form onSubmit={submit} className="mt-5 space-y-4">
          <div>
            <label className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              Dominio *
            </label>
            <input
              type="text"
              required
              value={domain}
              onChange={(e) => handleDomainChange(e.target.value)}
              placeholder="agendasolar.it"
              className="mt-1 w-full rounded-lg bg-surface-container-low px-3 py-2.5 text-sm font-mono text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
            />
          </div>

          <div>
            <label className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              Utilizzo
            </label>
            <div className="mt-2 grid grid-cols-2 gap-2">
              {(['outreach', 'brand'] as const).map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPurpose(p)}
                  className={`rounded-lg border px-3 py-2.5 text-left text-sm transition-colors ${
                    purpose === p
                      ? 'border-primary bg-primary-container/30 font-semibold text-primary'
                      : 'border-outline-variant text-on-surface hover:bg-surface-container-low'
                  }`}
                >
                  <p className="font-semibold capitalize">{p === 'outreach' ? 'Outreach cold' : 'Brand / transactional'}</p>
                  <p className="mt-0.5 text-[11px] text-on-surface-variant">
                    {p === 'outreach' ? 'Gmail OAuth · cold B2B' : 'Resend · notifiche, login'}
                  </p>
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              Tracking host{' '}
              <span className="normal-case font-normal text-on-surface-variant/60">(CNAME a track.solarld.app)</span>
            </label>
            <input
              type="text"
              value={trackingHost}
              onChange={(e) => setTrackingHost(e.target.value)}
              placeholder={`go.${domain || 'tuodominio.it'}`}
              className="mt-1 w-full rounded-lg bg-surface-container-low px-3 py-2.5 text-sm font-mono text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
            />
            <p className="mt-1 text-[11px] text-on-surface-variant">
              Ogni dominio traccia i click sui propri link (click, optout, lead portal).
              Imposta <span className="font-mono">go.{domain || 'tuodominio.it'}</span> come
              CNAME verso <span className="font-mono">track.solarld.app</span>.
            </p>
          </div>

          {error && (
            <p className="rounded-lg bg-error-container px-3 py-2 text-sm text-on-error-container">
              {error}
            </p>
          )}

          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 rounded-full border border-outline-variant px-4 py-2.5 text-sm font-semibold text-on-surface hover:bg-surface-container-low"
            >
              Annulla
            </button>
            <button
              type="submit"
              disabled={loading}
              className="flex-1 rounded-full bg-gradient-primary px-4 py-2.5 text-sm font-bold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {loading ? 'Creazione…' : 'Aggiungi dominio'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
