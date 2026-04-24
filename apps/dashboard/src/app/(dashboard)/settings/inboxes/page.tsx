'use client';

/**
 * /settings/inboxes — Multi-inbox management
 *
 * Lists all configured sending inboxes with live send counts and pause status.
 * Allows creating, editing, pausing/unpausing, and deleting inboxes.
 *
 * Design notes:
 *   - Each inbox is an independent sending identity on the tenant's verified
 *     domain (e.g. giuseppe@acme.it, marco@acme.it, info@acme.it).
 *   - The InboxSelector in the API round-robins sends across all active,
 *     non-capped inboxes. If one inbox hits its daily_cap or gets auto-paused
 *     after a Resend error, the others carry the load uninterrupted.
 *   - 250 emails/day = 5 inboxes × 50/day. Adjust daily_cap per inbox.
 */

import { useEffect, useState, useTransition } from 'react';
import { BentoCard } from '@/components/ui/bento-card';
import { apiClient } from '@/lib/api-client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface InboxRow {
  id: string;
  email: string;
  display_name: string;
  reply_to_email: string | null;
  daily_cap: number;
  total_sent_today: number;
  remaining_today: number;
  is_paused: boolean;
  paused_until: string | null;
  pause_reason: string | null;
  last_sent_at: string | null;
  active: boolean;
  created_at: string;
  // Sprint 6.1 provider fields
  provider: 'resend' | 'gmail_oauth' | 'm365_oauth' | 'smtp';
  oauth_account_email: string | null;
  oauth_connected_at: string | null;
  oauth_last_error: string | null;
  oauth_last_error_at: string | null;
}

interface QuotaSummary {
  total_daily_cap: number;
  sent_today: number;
  remaining_today: number;
  active_inboxes: number;
  paused_inboxes: number;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function InboxesPage() {
  const [inboxes, setInboxes] = useState<InboxRow[]>([]);
  const [quota, setQuota] = useState<QuotaSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [isPending, startTransition] = useTransition();

  async function refresh() {
    try {
      const [inboxRes, quotaRes] = await Promise.all([
        apiClient.get<{ inboxes: InboxRow[] }>('/v1/inboxes'),
        apiClient.get<QuotaSummary>('/v1/inboxes/quota'),
      ]);
      setInboxes(inboxRes.inboxes);
      setQuota(quotaRes);
    } catch (e) {
      setError('Errore nel caricamento delle inbox');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []);

  async function handleUnpause(inboxId: string) {
    startTransition(async () => {
      await apiClient.post(`/v1/inboxes/${inboxId}/unpause`, {});
      await refresh();
    });
  }

  async function handleDelete(inboxId: string, email: string) {
    if (!confirm(`Eliminare definitivamente l'inbox ${email}?`)) return;
    startTransition(async () => {
      await apiClient.delete(`/v1/inboxes/${inboxId}`);
      await refresh();
    });
  }

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <header className="flex items-end justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Impostazioni · Distribuzione invii
          </p>
          <h1 className="mt-1 font-headline text-4xl font-bold tracking-tighter">
            Inbox mittenti
          </h1>
          <p className="mt-2 max-w-xl text-sm text-on-surface-variant">
            Più indirizzi email sullo stesso dominio verificato. Il sistema
            distribuisce gli invii in round-robin rispettando il cap giornaliero
            per inbox. Se un&apos;inbox riceve un errore dal provider viene messa
            in pausa automatica senza bloccare le altre.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowForm(true)}
          className="rounded-full bg-gradient-primary px-5 py-2.5 text-sm font-bold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-90"
        >
          + Aggiungi inbox
        </button>
      </header>

      {/* Quota summary strip */}
      {quota && (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <QuotaChip label="Cap totale / giorno" value={quota.total_daily_cap.toString()} />
          <QuotaChip label="Inviate oggi" value={quota.sent_today.toString()} />
          <QuotaChip
            label="Rimanenti oggi"
            value={quota.remaining_today.toString()}
            accent={quota.remaining_today === 0 ? 'error' : 'primary'}
          />
          <QuotaChip
            label="In pausa"
            value={quota.paused_inboxes.toString()}
            accent={quota.paused_inboxes > 0 ? 'warn' : 'neutral'}
          />
        </div>
      )}

      {/* Inbox list */}
      {loading ? (
        <p className="text-sm text-on-surface-variant">Caricamento…</p>
      ) : error ? (
        <p className="text-sm text-error">{error}</p>
      ) : inboxes.length === 0 ? (
        <BentoCard span="full">
          <div className="py-8 text-center">
            <p className="font-headline text-xl font-bold text-on-surface">
              Nessuna inbox configurata
            </p>
            <p className="mt-2 text-sm text-on-surface-variant">
              Aggiungi la prima inbox per distribuire gli invii su più mittenti.
              Ogni inbox condivide il dominio verificato del tenant.
            </p>
            <button
              type="button"
              onClick={() => setShowForm(true)}
              className="mt-6 rounded-full border border-primary px-5 py-2 text-sm font-semibold text-primary hover:bg-primary/10"
            >
              Aggiungi la prima inbox
            </button>
          </div>
        </BentoCard>
      ) : (
        <div className="space-y-3">
          {inboxes.map((inbox) => (
            <InboxCard
              key={inbox.id}
              inbox={inbox}
              isPending={isPending}
              onUnpause={() => handleUnpause(inbox.id)}
              onDelete={() => handleDelete(inbox.id, inbox.email)}
              onRefresh={refresh}
            />
          ))}
        </div>
      )}

      {/* Create form modal */}
      {showForm && (
        <CreateInboxModal
          onClose={() => setShowForm(false)}
          onCreated={async () => { setShowForm(false); await refresh(); }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// InboxCard
// ---------------------------------------------------------------------------

function InboxCard({
  inbox,
  isPending,
  onUnpause,
  onDelete,
  onRefresh,
}: {
  inbox: InboxRow;
  isPending: boolean;
  onUnpause: () => void;
  onDelete: () => void;
  onRefresh: () => void;
}) {
  const [connectingGmail, setConnectingGmail] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);

  const capPct = inbox.daily_cap > 0
    ? Math.min(100, Math.round((inbox.total_sent_today / inbox.daily_cap) * 100))
    : 0;

  const hasOAuthError = !!inbox.oauth_last_error;
  const isGmailConnected = inbox.provider === 'gmail_oauth' && !!inbox.oauth_connected_at && !hasOAuthError;
  const isGmailExpired = inbox.provider === 'gmail_oauth' && hasOAuthError;

  async function handleConnectGmail() {
    setConnectingGmail(true);
    try {
      const res = await apiClient.post<{ authorize_url: string }>(
        `/v1/inboxes/${inbox.id}/oauth/gmail/authorize`, {}
      );
      // Open the Google OAuth consent screen in a popup/same tab.
      window.location.href = res.authorize_url;
    } catch {
      setConnectingGmail(false);
    }
  }

  async function handleDisconnect() {
    if (!confirm('Disconnettere Gmail da questa inbox? Tornerà a usare Resend.')) return;
    setDisconnecting(true);
    try {
      await apiClient.post(`/v1/inboxes/${inbox.id}/oauth/disconnect`, {});
      onRefresh();
    } finally {
      setDisconnecting(false);
    }
  }

  return (
    <div className={`rounded-xl border px-5 py-4 ${
      isGmailExpired
        ? 'border-tertiary/40 bg-tertiary-container/10'
        : 'border-outline-variant/40 bg-surface-container-lowest'
    }`}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          {/* Header row */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm font-semibold text-on-surface">
              {inbox.email}
            </span>
            {inbox.display_name && (
              <span className="text-xs text-on-surface-variant">
                · {inbox.display_name}
              </span>
            )}

            {/* Provider badge */}
            <ProviderBadge
              provider={inbox.provider}
              connected={isGmailConnected ?? false}
              expired={isGmailExpired}
            />

            {!inbox.active && (
              <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
                Disattivata
              </span>
            )}
            {inbox.is_paused && inbox.active && (
              <span className="rounded-full bg-error-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-error-container">
                In pausa
              </span>
            )}
            {!inbox.is_paused && inbox.active && (
              <span className="rounded-full bg-primary-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-primary-container">
                Attiva
              </span>
            )}
          </div>

          {/* OAuth account info */}
          {inbox.oauth_account_email && (
            <p className="mt-1 text-xs text-on-surface-variant">
              Account: <span className="font-mono">{inbox.oauth_account_email}</span>
              {inbox.oauth_connected_at && (
                <span>
                  {' · '}Connesso il{' '}
                  {new Date(inbox.oauth_connected_at).toLocaleDateString('it-IT')}
                </span>
              )}
            </p>
          )}

          {/* OAuth error banner */}
          {isGmailExpired && inbox.oauth_last_error && (
            <p className="mt-1 text-xs text-tertiary">
              ⚠️ Token scaduto o revocato:{' '}
              <span className="font-mono">{inbox.oauth_last_error}</span>.{' '}
              Ri-autorizza per continuare a inviare via Gmail.
            </p>
          )}

          {/* Pause info */}
          {inbox.is_paused && inbox.paused_until && (
            <p className="mt-1 text-xs text-error">
              Pausa fino a {new Date(inbox.paused_until).toLocaleTimeString('it-IT', {
                hour: '2-digit', minute: '2-digit',
              })}
              {inbox.pause_reason ? ` · ${inbox.pause_reason}` : ''}
            </p>
          )}

          {/* Daily cap progress bar */}
          <div className="mt-3 flex items-center gap-3">
            <div className="flex-1 overflow-hidden rounded-full bg-surface-container-high">
              <div
                className={`h-2 rounded-full transition-all ${
                  capPct >= 100 ? 'bg-error' : capPct >= 80 ? 'bg-tertiary' : 'bg-primary/60'
                }`}
                style={{ width: `${capPct}%` }}
              />
            </div>
            <span className="shrink-0 text-xs tabular-nums text-on-surface-variant">
              {inbox.total_sent_today} / {inbox.daily_cap} oggi
            </span>
          </div>
        </div>

        {/* Actions */}
        <div className="flex shrink-0 flex-col items-end gap-2">
          {/* Gmail OAuth connect / re-auth */}
          {(inbox.provider !== 'gmail_oauth' || isGmailExpired) && (
            <button
              type="button"
              onClick={handleConnectGmail}
              disabled={connectingGmail || isPending}
              className="flex items-center gap-1.5 rounded-lg border border-primary/60 bg-primary-container/20 px-3 py-1.5 text-xs font-semibold text-primary hover:bg-primary/10 disabled:opacity-50"
            >
              <svg className="h-3 w-3" viewBox="0 0 24 24" fill="currentColor">
                <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
              </svg>
              {isGmailExpired ? 'Ri-autorizza Gmail' : 'Connetti Gmail'}
            </button>
          )}

          {/* Disconnect Gmail */}
          {isGmailConnected && (
            <button
              type="button"
              onClick={handleDisconnect}
              disabled={disconnecting || isPending}
              className="rounded-lg border border-outline-variant px-3 py-1.5 text-xs font-semibold text-on-surface-variant hover:bg-surface-container-low disabled:opacity-50"
            >
              {disconnecting ? 'Disconnessione…' : 'Disconnetti Gmail'}
            </button>
          )}

          {inbox.is_paused && (
            <button
              type="button"
              onClick={onUnpause}
              disabled={isPending}
              className="rounded-lg border border-primary px-3 py-1.5 text-xs font-semibold text-primary hover:bg-primary/10 disabled:opacity-50"
            >
              Sblocca
            </button>
          )}
          <button
            type="button"
            onClick={onDelete}
            disabled={isPending}
            className="rounded-lg border border-error/40 px-3 py-1.5 text-xs font-semibold text-error/80 hover:bg-error/10 disabled:opacity-50"
          >
            Elimina
          </button>
        </div>
      </div>
    </div>
  );
}

function ProviderBadge({
  provider,
  connected,
  expired,
}: {
  provider: string;
  connected: boolean;
  expired: boolean;
}) {
  if (provider === 'gmail_oauth') {
    if (expired) {
      return (
        <span className="rounded-full bg-tertiary-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-tertiary-container">
          Gmail · Token scaduto
        </span>
      );
    }
    if (connected) {
      return (
        <span className="rounded-full bg-primary-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-primary-container">
          Gmail OAuth ✓
        </span>
      );
    }
    return (
      <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
        Gmail · Da collegare
      </span>
    );
  }
  return (
    <span className="rounded-full border border-outline-variant/40 px-2 py-0.5 text-[10px] font-mono text-on-surface-variant">
      {provider}
    </span>
  );
}

// ---------------------------------------------------------------------------
// QuotaChip
// ---------------------------------------------------------------------------

function QuotaChip({
  label,
  value,
  accent = 'neutral',
}: {
  label: string;
  value: string;
  accent?: 'primary' | 'error' | 'warn' | 'neutral';
}) {
  const valueClass = {
    primary: 'text-primary',
    error: 'text-error',
    warn: 'text-tertiary',
    neutral: 'text-on-surface',
  }[accent];

  return (
    <div className="rounded-lg bg-surface-container-low px-4 py-3">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
        {label}
      </p>
      <p className={`mt-1 font-headline text-2xl font-bold tabular-nums tracking-tighter ${valueClass}`}>
        {value}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CreateInboxModal
// ---------------------------------------------------------------------------

function CreateInboxModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => Promise<void>;
}) {
  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [dailyCap, setDailyCap] = useState(50);
  const [replyTo, setReplyTo] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await apiClient.post('/v1/inboxes', {
        email: email.trim().toLowerCase(),
        display_name: displayName.trim(),
        daily_cap: dailyCap,
        reply_to_email: replyTo.trim() || null,
      });
      await onCreated();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Errore nella creazione';
      setError(msg);
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
          Aggiungi inbox mittente
        </h2>
        <p className="mt-1 text-sm text-on-surface-variant">
          L&apos;indirizzo deve essere sul dominio verificato del tuo tenant.
        </p>

        <form onSubmit={submit} className="mt-5 space-y-4">
          <div>
            <label className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              Indirizzo email *
            </label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="giuseppe@tuodominio.it"
              className="mt-1 w-full rounded-lg bg-surface-container-low px-3 py-2.5 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
            />
          </div>

          <div>
            <label className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              Nome mittente
            </label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Giuseppe Rossi"
              maxLength={120}
              className="mt-1 w-full rounded-lg bg-surface-container-low px-3 py-2.5 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
            />
          </div>

          <div>
            <label className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              Cap giornaliero
            </label>
            <input
              type="number"
              required
              min={1}
              max={2000}
              value={dailyCap}
              onChange={(e) => setDailyCap(Number(e.target.value))}
              className="mt-1 w-full rounded-lg bg-surface-container-low px-3 py-2.5 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
            />
            <p className="mt-1 text-[11px] text-on-surface-variant">
              Max email che questa inbox può inviare al giorno. 50 = default sicuro.
            </p>
          </div>

          <div>
            <label className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              Reply-to (opzionale)
            </label>
            <input
              type="email"
              value={replyTo}
              onChange={(e) => setReplyTo(e.target.value)}
              placeholder="info@tuodominio.it"
              className="mt-1 w-full rounded-lg bg-surface-container-low px-3 py-2.5 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
            />
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
              {loading ? 'Creazione…' : 'Crea inbox'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
